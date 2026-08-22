# Akrasia_Zero

Personal execution system for a single operator. It is not a team
tracker, calendar, or general-purpose task manager.

The working loop is:

1. **Task List** (`/`) — durable task tree
2. **Today’s Tasks** (`/today/page`) — what is queued for the local calendar day
3. **Session Timer** (`/timer/`) — one focus session at a time
4. **Review** (`/review/`) — look back over completed sessions

Three records carry that loop:

- **Task** — a durable item in the tree (title, status, optional parent)
- **DailyTask** — a Task queued on a specific local date, with planned sessions and queue order
- **WorkSession** — one timed focus attempt against a DailyTask

The application is single-user by design. There is no authentication
and no multi-user model.

## Architecture

- FastAPI application (`app/`) served by Uvicorn
- Jinja2 HTML templates with HTMX for in-page updates
- Small client-side JavaScript for timer and break countdown
  (`app/static/timer.js`, `app/static/break.js`)
- PostgreSQL 17
- SQLAlchemy 2.x
- Alembic migrations under `migrations/`
- Docker Compose for local run and isolated tests
- Python 3.13 in the application image

Settings live in `app/config.py`. Time helpers live in `app/time.py`.

## Project identity

The product name is **Akrasia_Zero**.

Some local infrastructure still uses the legacy `system1` identifier:
the project directory, PostgreSQL database/user names (`system1`,
`system1_test`), Docker container names (`system1-web`, `system1-db`,
`system1-test`, …), and volumes. That is intentional and does not
indicate a second application.

The Python package remains `app`. Do not rename those infrastructure
identifiers without a dedicated migration plan.

## Local setup

From the repository root.

Build and start the app and database:

```bash
docker compose up --build -d
docker compose exec web alembic upgrade head
```

Then open http://localhost:8000

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

After changing pinned dependencies, rebuild:

```bash
docker compose up --build -d
```

`docker compose down` stops containers and removes them but keeps named
volumes unless you pass `-v`. Do not use `-v` unless you intend to
wipe local Postgres data.

## Configuration

Runtime settings are environment variables, read by `app/config.py`.
Docker Compose sets them explicitly for `web` and `test`.
`.env.example` lists the same keys with safe placeholders. Compose
does not currently load `.env.example` into the containers; copy
values into Compose (or a private `.env` you do not commit) if you
run outside Compose.

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `Akrasia_Zero` | UI and OpenAPI title |
| `DISPLAY_NAME` | `Matt Stone` | Single-user label in the shell |
| `APP_TIMEZONE` | `Europe/London` | Local calendar day for Today, Timer, Review |
| `FOCUS_SESSION_MINUTES` | `25` | Default focus length |
| `BREAK_DURATION_MINUTES` | `5` | Break after a committed session |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `DATABASE_URL` | none | Required when the app uses a database. No silent production default. |

`DATABASE_URL` uses the SQLAlchemy form, for example
`postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME`. Do not commit
real secrets. `.gitignore` ignores `.env`.

## Tests

Tests run in an isolated Compose profile against PostgreSQL database
`system1_test` only. The suite refuses any other database name.

First run (rebuilds the test image):

```bash
docker compose --profile test run --rm --build test
```

Later runs, when image and dependencies are unchanged:

```bash
docker compose --profile test run --rm test
```

The test container command is `pytest`. The suite includes Alembic
verification: upgrade a fresh empty database to head, downgrade to
base, upgrade again, and check the resulting schema.

## Migrations

Generate and apply schema changes with Alembic. Revision files live in
`migrations/versions/`.

Apply the current head to the running app database:

```bash
docker compose exec web alembic upgrade head
```

Discipline:

1. Change the SQLAlchemy model / schema rules
2. Add an Alembic revision
3. Upgrade
4. Run the full test suite

A fresh database is reproducible from Alembic (`upgrade head`). The
test suite proves that path. There is no separate production migrate
job in this repository yet; do not assume one.

## Dependency management

- `requirements.txt` — runtime only, versions pinned
- `requirements-dev.txt` — runtime plus pytest and httpx

The web image installs `requirements.txt` only. The test image installs
`requirements-dev.txt`.

To change a direct dependency: edit the pin, rebuild, run
`docker compose --profile test run --rm --build test`, then commit the
new pin. Do not upgrade casually. Transitive dependencies are not
fully pinned.

## Logging

Logs go to stdout/stderr for Docker to collect. There is no log file
and no external logging service.

Logged:

- startup (app name, timezone, focus/break minutes, log level, database name and host — not credentials)
- shutdown
- Uvicorn access lines (method, path, status)
- warnings when a unique constraint rejects a concurrent session start or add-to-day race
- unhandled exceptions, with stack traces (Uvicorn/Starlette)

Not logged:

- task titles or descriptions
- session notes or other free text
- request bodies
- passwords, usernames, or full `DATABASE_URL`
- cookies or authorization headers

Expected business conflicts (409: empty title, second running session,
invalid outcome, and similar) are not treated as errors.

## Core invariants

These are enforced in application code and covered by tests:

- A Task cannot be completed while it has active descendants.
- The executable daily queue is DailyTasks that are still `planned` whose underlying Task is `active`. Historical DailyTask rows remain; they drop out of the queue.
- At most one WorkSession may be `running` (partial unique index, plus application checks).
- Committing a session that completes or abandons a task updates Task, DailyTask, and WorkSession in one transaction; a later failure rolls all of it back.
- At most one DailyTask exists per Task and date.
- Timestamps are stored timezone-aware UTC.
- “Today” and local day bounds use `APP_TIMEZONE` (DST-aware).
- A DailyTask with a running WorkSession cannot be removed from the day.
- Task titles are validated server-side (non-empty, max 200 characters). Planned session counts must be at least 1 when provided. Session duration must be at least 1 minute.

## Scope and future direction

Akrasia_Zero is the execution and data layer: capture work, run
sessions, keep an honest queue.

Future analytical or agent work belongs to **Enkrateia_One**, not this
process. Agents should use APIs, not connect to the database directly.
Retrieval for agents should be bounded or aggregated rather than
dumping full history by default.

That API is not specified here.

## Milestone status

Milestone 6A (hardening: timezone-aware timestamps, one running
session, atomic complete/abandon, executable-queue rules, Alembic
verification) and Milestone 6B (settings, identity, pinned
dependencies, logging, this README) are complete.
