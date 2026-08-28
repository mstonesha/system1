# First VPS deployment

This is the operator checklist for a first personal VPS. The
repository now includes production Compose, Caddy, secrets
conventions, health checks, and backup/restore scripts. It does
**not** mean a server has been provisioned. Do not treat this file
as a completed deployment.

Akrasia_Zero is intended to be reachable on the public internet
over HTTPS. Browser session authentication protects the HTML UI.
Bearer authentication protects `/api/analysis/*`. PostgreSQL and
Uvicorn stay on the internal Docker network.

A future VPS may also host Hermes and n8n. Those services are not
part of this stack. They should remain separate and must not be
publicly exposed by default.

## Prerequisites

- Linux VPS with Docker and Docker Compose
- SSH key access to that host
- A DNS A/AAAA record pointing the chosen hostname at the VPS
  **before** certificate issuance
- Production secrets prepared (see below)

Do not start Caddy against a real hostname until DNS points at
this machine. Let's Encrypt issuance will otherwise fail.

## Secrets

Copy `.env.production.example` to `.env.production` on the VPS.
`.env.production` is gitignored. Never commit it.

Required:

- `AKRASIA_DOMAIN` — hostname only, for example `tasks.example.com`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD` — URL-safe (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `POSTGRES_DB`
- `AKRASIA_PASSWORD_HASH` — `python -m app.auth.password` (prints the hash only)
- `AKRASIA_ANALYSIS_API_TOKEN` — `python -c "import secrets; print(secrets.token_urlsafe(48))"`

`docker-compose.prod.yml` constructs `DATABASE_URL` from the
`POSTGRES_*` values (host `db` on the internal network) and forces
`AKRASIA_COOKIE_SECURE=true`.

Optional application settings: `APP_NAME`, `DISPLAY_NAME`,
`APP_TIMEZONE`, `FOCUS_SESSION_MINUTES`, `BREAK_DURATION_MINUTES`,
`LOG_LEVEL`.

Optional and **not** required for production runtime:
`EVAL_AGENT_API_KEY`, `EVAL_AGENT_BASE_URL`, `EVAL_AGENT_MODEL`.
Leave them unset on the VPS.

## First deployment

From the repository root on the VPS:

```bash
cp .env.production.example .env.production
# edit .env.production with real values

docker compose -f docker-compose.prod.yml --env-file .env.production up -d db
docker compose -f docker-compose.prod.yml --env-file .env.production run --rm web alembic upgrade head
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Then:

1. Confirm `https://$AKRASIA_DOMAIN/health` returns `{"status":"ok"}`
2. Sign in at `/login`
3. Call `/api/analysis/*` with `Authorization: Bearer <token>`
4. Confirm `http://` redirects to `https://`

Migrations are **not** run automatically on web-container start.

## Update procedure

1. Backup the database (`deploy/backup-db.sh`) and copy the dump off the VPS
2. `git pull`
3. `docker compose -f docker-compose.prod.yml --env-file .env.production build web`
4. `docker compose -f docker-compose.prod.yml --env-file .env.production run --rm web alembic upgrade head`
5. `docker compose -f docker-compose.prod.yml --env-file .env.production up -d`
6. Verify `/health`, login, and API auth

## Backup

```bash
chmod +x deploy/backup-db.sh deploy/restore-db.sh
./deploy/backup-db.sh
```

Writes `backups/akrasia-YYYYMMDDTHHMMSSZ.dump` (custom-format
`pg_dump`). Override with `BACKUP_DIR` and `ENV_FILE` if needed.
The script runs `pg_dump` inside the `db` container so the
password is not placed on the host command line.

PostgreSQL data lives in the named volume `postgres_prod_data`
(Compose prefixes the project directory, typically
`system1_postgres_prod_data`). Do not delete that volume.

A dump that exists only on the VPS is not a backup strategy.
Keep an off-VPS copy (home server, object storage, or another
encrypted location). Hostinger snapshots are supplementary, not
the only copy.

Cron/systemd scheduling is not configured here.

## Restore

Stop is handled by the helper (it stops `web`, restores, starts
`web`):

```bash
./deploy/restore-db.sh backups/akrasia-YYYYMMDDTHHMMSSZ.dump
```

This overwrites the production database. Restore was verified
automatically against an isolated `system1_restore_verify`
database in the test suite, not against development or production
data.

## Network

Public:

- Caddy `80` / `443` only

Docker-internal:

- `frontend` network: Caddy → `web:8000`
- `backend` network: `web` → PostgreSQL
- PostgreSQL is not published
- Uvicorn is not published

Future private/Tailscale (not installed in this stack):

- SSH
- Hermes internal/admin interfaces
- n8n admin interface
- home server ↔ VPS traffic

Do not expose PostgreSQL over Tailscale by default.

## Security checklist

Host firewall and SSH are operator work. This repository does not
apply host firewall rules.

- Use SSH key authentication
- Disable password SSH login after key access is verified
- Consider disabling direct root SSH where practical
- Permit `80`/`443` publicly
- Permit SSH only as needed; after Tailscale, prefer SSH on the
  private network
- Do not publish PostgreSQL or the Uvicorn port
- Confirm `AKRASIA_COOKIE_SECURE` is true (Compose forces it)

Intentionally unauthenticated HTTP routes: `GET /health`,
`GET /login`, `POST /login`, `GET /static/*`. FastAPI `/docs` and
`/openapi.json` are also unauthenticated; they do not mutate
application state. HTML application routes require a browser
session. `/api/analysis/*` requires the bearer token.

## Local production-stack smoke test

Do not contact Let's Encrypt. Use an HTTP site address:

```bash
# temporary env with AKRASIA_DOMAIN=http://localhost
docker compose -f docker-compose.prod.yml --env-file <http-only-env> config
```

A full `up` binds host ports 80 and 443. That is optional and is
not required to validate this milestone.

## Compose validation

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production.example config
```

Do not merge `docker-compose.yml` with the production file.
Development Compose publishes Uvicorn `:8000` and bind-mounts the
working tree.
