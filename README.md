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

There is no authentication and no multi-user model.

## Current status

Implemented today:

- Core execution loop (tasks, Today, timer, review)
- Deterministic analytics library
- Synthetic evaluation datasets A–F
- Frozen evidence/prompt contracts and an isolated evaluation runner

Not implemented:

- Production deployment hardening
- Authentication, HTTPS, backups
- **Enkrateia_One** (the intended future analytical/agent layer)
- A production API for agents
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
- Docker Compose for local run, tests, and evaluation
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

This repository does not document production deployment.

## Repository map

| Path | Role |
|---|---|
| `app/` | FastAPI application: routes, services, models, templates |
| `app/analytics/` | Deterministic calculation library (not shown in the UI) |
| `evaluation/` | Isolated synthetic-dataset generator |
| `evaluation/agent/` | Evidence contracts, prompts, model client, runner |
| `evaluation/scenarios/` | Dataset generators A–F |
| `evaluation/ground_truth/` | Hidden operator YAML (never sent to the model) |
| `tests/` | pytest suite, including analytics and evaluation |
| `migrations/` | Alembic revisions |

## Further reading

- [Architecture](docs/architecture.md)
- [Agent evaluation](docs/evaluation.md)
- [Development status](docs/development-status.md)
