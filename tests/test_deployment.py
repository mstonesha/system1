"""Production Compose, image, and secret-file conventions."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"
ENV_EXAMPLE = REPO_ROOT / ".env.production.example"
GITIGNORE = REPO_ROOT / ".gitignore"
BACKUP_SCRIPT = REPO_ROOT / "deploy" / "backup-db.sh"
RESTORE_SCRIPT = REPO_ROOT / "deploy" / "restore-db.sh"


PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace.example.com",
    "example.com",
)


def _prod():
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))


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
        CADDYFILE,
        ENV_EXAMPLE,
        BACKUP_SCRIPT,
        RESTORE_SCRIPT,
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
    for text in (backup, restore):
        assert "set -eu" in text
        assert "-W" not in text
        assert "--password" not in text
        assert "$POSTGRES_PASSWORD" not in text
        assert "PGPASSWORD=" not in text
    assert "pg_dump" in backup
    assert "pg_restore" in restore
