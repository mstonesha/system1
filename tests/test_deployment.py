"""Production Compose, image, and secret-file conventions."""

import os
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
HOSTINGER_COMPOSE = REPO_ROOT / "docker-compose.hostinger.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"
ENV_EXAMPLE = REPO_ROOT / ".env.production.example"
GITIGNORE = REPO_ROOT / ".gitignore"
BACKUP_SCRIPT = REPO_ROOT / "deploy" / "backup-db.sh"
RESTORE_SCRIPT = REPO_ROOT / "deploy" / "restore-db.sh"
COMPOSE_HELPER = REPO_ROOT / "deploy" / "prod-compose.sh"


PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace.example.com",
    "example.com",
)


def _prod():
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))


def _hostinger():
    return yaml.safe_load(HOSTINGER_COMPOSE.read_text(encoding="utf-8"))


def test_production_compose_does_not_publish_db_or_uvicorn():
    services = _prod()["services"]
    assert "ports" not in services["db"]
    assert "ports" not in services["web"]
    caddy_ports = services["caddy"]["ports"]
    assert "80:80" in caddy_ports
    assert "443:443" in caddy_ports
    assert len(caddy_ports) == 2


def test_production_compose_networks_keep_db_off_frontend():
    services = _prod()["services"]
    assert services["db"]["networks"] == ["backend"]
    assert "frontend" in services["web"]["networks"]
    assert "backend" in services["web"]["networks"]
    assert services["caddy"]["networks"] == ["frontend"]


def test_production_web_command_has_no_reload():
    command = _prod()["services"]["web"]["command"]
    assert "--reload" not in command
    assert "--proxy-headers" in command
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "--reload" not in dockerfile.split("CMD", 1)[1]
    dev = yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8"))
    assert "--reload" in dev["services"]["web"]["command"]


def test_production_forces_secure_cookies():
    env = _prod()["services"]["web"]["environment"]
    assert env["AKRASIA_COOKIE_SECURE"] == "true"
    overlay_web = _hostinger()["services"]["web"]
    assert "environment" not in overlay_web


def test_hostinger_overlay_disables_caddy_and_adds_traefik_labels():
    overlay = _hostinger()
    services = overlay["services"]
    assert set(services) <= {"caddy", "web", "db"}
    assert "hermes" not in services
    assert "n8n" not in services
    assert "caddy" in services
    assert "web" in services

    caddy = services["caddy"]
    assert caddy["profiles"] == ["caddy"]
    assert caddy["ports"] == []

    web = services["web"]
    assert "ports" not in web
    assert "db" not in services or "ports" not in services["db"]
    labels = web["labels"]
    joined = "\n".join(str(item) for item in labels)
    assert "traefik.enable=true" in joined
    assert "traefik.docker.network=traefik-proxy" in joined
    assert "Host(`${AKRASIA_DOMAIN}`)" in joined
    assert "entrypoints=websecure" in joined
    assert "tls.certresolver=letsencrypt" in joined
    assert "loadbalancer.server.port=8000" in joined
    assert "frontend" in web["networks"]
    assert "backend" in web["networks"]
    assert "traefik-proxy" in web["networks"]

    network = overlay["networks"]["traefik-proxy"]
    assert network["external"] is True
    assert network.get("name", "traefik-proxy") == "traefik-proxy"


def test_hostinger_overlay_does_not_publish_web_or_db():
    prod = _prod()["services"]
    overlay = _hostinger()["services"]
    assert "ports" not in prod["web"]
    assert "ports" not in prod["db"]
    assert "ports" not in overlay["web"]
    assert "db" not in overlay or "ports" not in overlay["db"]
    assert overlay["caddy"]["ports"] == []


def test_production_web_does_not_embed_db_password_in_url():
    env = _prod()["services"]["web"]["environment"]
    assert "DATABASE_URL" not in env
    assert env["POSTGRES_USER"] == "${POSTGRES_USER}"
    assert env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD}"
    assert env["POSTGRES_DB"] == "${POSTGRES_DB}"
    joined = " ".join(str(value) for value in env.values())
    assert "postgresql+" not in joined


def test_production_healthchecks_and_restart_policies():
    services = _prod()["services"]
    for name in ("web", "db", "caddy"):
        assert services[name]["restart"] == "unless-stopped", name
    assert "healthcheck" in services["web"]
    assert "healthcheck" in services["db"]
    assert "/health" in str(services["web"]["healthcheck"]["test"])
    assert "pg_isready" in str(services["db"]["healthcheck"]["test"])


def test_production_web_has_no_bind_mount():
    web = _prod()["services"]["web"]
    assert "volumes" not in web


def test_production_uses_named_postgres_volume():
    volumes = _prod()["volumes"]
    assert "postgres_prod_data" in volumes
    db_volumes = _prod()["services"]["db"]["volumes"]
    assert any(
        item.startswith("postgres_prod_data:")
        for item in db_volumes
    )


def test_caddyfile_uses_configurable_domain():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "{$AKRASIA_DOMAIN}" in text
    assert "reverse_proxy web:8000" in text
    site_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert site_lines[0].startswith("{$AKRASIA_DOMAIN}")
    lowered = text.lower()
    for token in ("akrasia.zero", "hostinger", "nexos"):
        assert token not in lowered


def test_dockerfile_includes_alembic_without_evaluation():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY ./migrations ./migrations" in text
    assert "COPY ./alembic.ini ./alembic.ini" in text
    assert "COPY ./app ./app" in text
    assert "evaluation" not in text
    assert "COPY ./tests" not in text


def test_production_env_example_is_placeholders_only():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "AKRASIA_DOMAIN=" in text
    assert "POSTGRES_PASSWORD=" in text
    assert "AKRASIA_PASSWORD_HASH=" in text
    assert "AKRASIA_ANALYSIS_API_TOKEN=" in text
    assert "$argon2id$" not in text
    assert "replace-with-an-argon2id-password-hash" in text
    assert "replace-with-a-long-random-secret" in text
    assert "replace-with-a-long-random-password" in text
    assert any(marker in text for marker in PLACEHOLDER_MARKERS)


def test_production_env_file_is_gitignored():
    text = GITIGNORE.read_text(encoding="utf-8")
    assert ".env.production" in text
    assert "backups/" in text


def test_tracked_deploy_files_contain_no_live_secrets():
    paths = (
        PROD_COMPOSE,
        HOSTINGER_COMPOSE,
        CADDYFILE,
        ENV_EXAMPLE,
        BACKUP_SCRIPT,
        RESTORE_SCRIPT,
        COMPOSE_HELPER,
        REPO_ROOT / ".env.example",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "$argon2id$v=" not in text, path
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() in {
                "AKRASIA_PASSWORD_HASH",
                "AKRASIA_ANALYSIS_API_TOKEN",
                "POSTGRES_PASSWORD",
                "EVAL_AGENT_API_KEY",
            }:
                assert value.strip() == "" or value.startswith(
                    "replace"
                ) or "placeholder" in value.lower() or "example" in value.lower(), (
                    path,
                    key,
                )


def test_backup_scripts_fail_closed_and_avoid_password_flags():
    backup = BACKUP_SCRIPT.read_text(encoding="utf-8")
    restore = RESTORE_SCRIPT.read_text(encoding="utf-8")
    helper = COMPOSE_HELPER.read_text(encoding="utf-8")
    for text in (backup, restore, helper):
        assert "-W" not in text
        assert "--password" not in text
        assert "$POSTGRES_PASSWORD" not in text
        assert "PGPASSWORD=" not in text
    for text in (backup, restore):
        assert "set -eu" in text
    assert "pg_dump" in backup
    assert "pg_restore" in restore
    assert "deploy/prod-compose.sh" in backup
    assert "deploy/prod-compose.sh" in restore
    assert "docker-compose.prod.yml" in helper
    assert "docker-compose.hostinger.yml" in helper


def _active_shell_lines(text):
    return [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _prod_compose_command(deployment=None):
    env = os.environ.copy()
    env.pop("AKRASIA_DEPLOYMENT", None)
    env.pop("ENV_FILE", None)
    if deployment is not None:
        env["AKRASIA_DEPLOYMENT"] = deployment
    return subprocess.run(
        [
            "sh",
            "-c",
            '. ./deploy/prod-compose.sh && printf %s "$COMPOSE"',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _compose_yml_files(command):
    files = []
    tokens = command.split()
    for index, token in enumerate(tokens):
        if token == "-f" and index + 1 < len(tokens):
            files.append(tokens[index + 1])
    return files


def test_generic_backup_restore_compose_uses_prod_only():
    for deployment in (None, "generic"):
        proc = _prod_compose_command(deployment)
        assert proc.returncode == 0, proc.stderr
        command = proc.stdout
        assert _compose_yml_files(command) == ["docker-compose.prod.yml"]
        assert "--env-file .env.production" in command
        assert "docker-compose.hostinger.yml" not in command


def test_hostinger_backup_restore_compose_uses_overlay():
    proc = _prod_compose_command("hostinger")
    assert proc.returncode == 0, proc.stderr
    command = proc.stdout
    assert _compose_yml_files(command) == [
        "docker-compose.prod.yml",
        "docker-compose.hostinger.yml",
    ]
    assert "--env-file .env.production" in command


def test_unknown_deployment_mode_fails_closed():
    proc = _prod_compose_command("caddy")
    assert proc.returncode != 0
    assert "unknown AKRASIA_DEPLOYMENT" in proc.stderr


def test_hostinger_restore_uses_overlay_and_cannot_recreate_generic_web():
    restore = RESTORE_SCRIPT.read_text(encoding="utf-8")
    active = "\n".join(_active_shell_lines(restore))
    assert "deploy/prod-compose.sh" in active
    assert "$COMPOSE stop web" in active
    assert "$COMPOSE start web" in active
    assert "$COMPOSE up" not in active
    assert "up -d" not in active
    assert "docker compose -f docker-compose.prod.yml --env-file" not in active

    proc = _prod_compose_command("hostinger")
    assert proc.returncode == 0, proc.stderr
    hostinger_cmd = proc.stdout
    generic_cmd = _prod_compose_command("generic").stdout
    assert "docker-compose.hostinger.yml" in hostinger_cmd
    assert "docker-compose.hostinger.yml" not in generic_cmd
    assert hostinger_cmd != generic_cmd
    # Restore starts web with $COMPOSE, so Hostinger cannot apply
    # the generic-only definition that omits Traefik labels/network.
    assert _compose_yml_files(generic_cmd) == ["docker-compose.prod.yml"]
    assert "docker-compose.hostinger.yml" in _compose_yml_files(
        hostinger_cmd
    )

