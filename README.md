# Akrasia_Zero

Personal task and execution system for a single operator. It is not a
team tracker, calendar, or finished product.

The working loop is:

1. **Task List** (`/`) — durable task tree
2. **Today’s Tasks** (`/today/page`) — queue for the local calendar day
3. **Session Timer** (`/timer/`) — one focus session at a time
4. **Review** (`/review/`) — look back over completed sessions

Three records carry that loop: **Task**, **DailyTask**, and
**WorkSession**.

The repository also contains deterministic analytics
(`app/analytics/`) and an experimental analytical-agent evaluation
layer (`evaluation/`). Analytics compute measurements; they do not
write conclusions. The evaluation harness tests whether a model can
read bounded evidence without inventing unsupported claims. That
harness is not a production agent.

There is a single-operator password for the HTML UI and a
separate machine bearer token for `/api/analysis/*`. There is no
multi-user model.

## Current status

Implemented today:

- Core execution loop (tasks, Today, timer, review)
- Deterministic analytics library
- Production analytical contract (`app/analysis/`)
- Local read-only analytical HTTP API (`/api/analysis/*`, also
  listed in FastAPI `/docs`)
- Machine bearer-token authentication for that JSON API
- Single-operator password login and server-side browser sessions
- Production Compose, Caddy/HTTPS config, health checks, and
  backup/restore procedures (VPS not provisioned from this repo)
- Synthetic evaluation datasets A–F
- Frozen evidence/prompt contracts and an isolated evaluation runner

The HTML UI requires a password. Store only an Argon2id hash in
`AKRASIA_PASSWORD_HASH`, never the raw password:

```bash
python -m app.auth.password
```

Paste the printed hash into the environment. Local HTTP must keep
`AKRASIA_COOKIE_SECURE=false`. Production HTTPS deployments must
set it `true`.

`GET /api/analysis/*` requires a separate machine token:

```
Authorization: Bearer <token>
```

The token comes from `AKRASIA_ANALYSIS_API_TOKEN`. If that
setting is unset or blank, those routes fail closed (401). They
are never anonymously available. Example:

```bash
curl -H "Authorization: Bearer replace-with-a-long-random-secret" \
  "http://localhost:8000/api/analysis/temporal?from_date=2026-01-12&to_date=2026-01-16"
```

The HTML UI (`/`, `/today/page`, `/timer/`, `/review/`) uses
server-side sessions, not that bearer token. Browser login does
not satisfy API auth, and the API token does not log anyone into
the UI. Authenticated HTML includes a per-session CSRF token for
forms and HTMX; the raw browser session token is never rendered
into the page. `POST /login` has no pre-login CSRF token; a
successful login always issues a fresh session. Production is
intended to be publicly reachable over HTTPS behind Caddy; see
[Deployment](docs/deployment.md). Local HTTP development keeps
`AKRASIA_COOKIE_SECURE=false`. The first VPS is not created by
this repository.

Not implemented:

- Provisioning a real VPS, Tailscale, Hermes, or n8n
- Multi-user accounts, OAuth, or password-reset email
- **Enkrateia_One** (the intended future analytical/agent layer)
- Agent memory, embeddings, or unrestricted database access

Some local infrastructure still uses the legacy `system1` identifier
(directory, Postgres names, Docker containers). That is intentional
and does not indicate a second application. The Python package is
`app`.

## Stack

- FastAPI, served by Uvicorn
- Jinja2 templates with HTMX for in-page updates
- PostgreSQL 17
- SQLAlchemy 2.x and Alembic
- Argon2id (`argon2-cffi`) for the operator password hash
- Docker Compose for local run, tests, evaluation, and production
- Caddy (production HTTPS reverse proxy)
- pytest
- Python 3.13 in the application image

There is no `pyproject.toml`. Pins live in `requirements.txt`
(runtime) and `requirements-dev.txt` (runtime plus pytest, httpx, and
PyYAML).

## Local development

From the repository root.

Start the app and database:

```bash
docker compose up --build -d
docker compose exec web alembic upgrade head
```

Then open http://localhost:8000

Analytical JSON endpoints are listed in FastAPI’s `/docs`
(`http://localhost:8000/docs`). They require a bearer token from
`AKRASIA_ANALYSIS_API_TOKEN`. Use the Authorize control in `/docs`
to supply that token for local testing. The HTML UI uses password
login instead. The app must not be exposed publicly.

Compose does not apply migrations on startup. A fresh Postgres volume
needs `alembic upgrade head` before the UI can use the schema.

Stop without destroying containers or volumes:

```bash
docker compose stop
```

Start them again:

```bash
docker compose start
```

Tests run in an isolated Compose profile against database
`system1_test` only:

```bash
docker compose --profile test run --rm --build test
```

Later runs, when the image is unchanged:

```bash
docker compose --profile test run --rm test
```

The test container command is `pytest`.

Synthetic evaluation datasets (isolated database
`system1_agent_eval`):

```bash
docker compose --profile eval run --rm eval
```

The default eval command generates `temporal_patterns`. Override as
needed, for example:

```bash
docker compose --profile eval run --rm eval python -m evaluation.generate interruptions_dependencies
docker compose --profile eval run --rm eval python -m evaluation.agent.runner --scenario temporal_patterns --dry-run
```

Live model calls require `EVAL_AGENT_API_KEY`, `EVAL_AGENT_BASE_URL`,
and `EVAL_AGENT_MODEL`. Dry-run builds evidence and renders the prompt
without calling a provider.

This repository documents a first-VPS procedure in
[Deployment](docs/deployment.md). It does not provision a server.

## Repository map

| Path | Role |
|---|---|
| `app/` | FastAPI application: routes, services, models, templates |
| `app/analytics/` | Deterministic calculation library (not shown in the UI) |
| `app/analysis/` | Approved read-only analytical contract |
| `app/auth/` | Operator password verification and browser sessions |
| `app/api/auth.py` | Bearer-token dependency for `/api/analysis/*` |
| `app/routes/auth.py` | `/login` and `/logout` |
| `app/routes/analysis.py` | JSON transport for that contract (machine auth) |
| `evaluation/` | Isolated synthetic-dataset generator |
| `evaluation/agent/` | Evidence contracts, prompts, model client, runner |
| `evaluation/scenarios/` | Dataset generators A–F |
| `evaluation/ground_truth/` | Hidden operator YAML (never sent to the model) |
| `deploy/` | Production Caddyfile and database backup/restore scripts |
| `docker-compose.prod.yml` | Standalone production stack (Caddy, web, db) |
| `tests/` | pytest suite, including analytics and evaluation |
| `migrations/` | Alembic revisions |

## Further reading

- [Architecture](docs/architecture.md)
- [Deployment](docs/deployment.md)
- [Agent evaluation](docs/evaluation.md)
- [Development status](docs/development-status.md)
