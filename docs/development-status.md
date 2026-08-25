# Development status

Snapshot of the repository as documented. This is not a backlog dump
and not a commercial product statement.

Akrasia_Zero remains a personal execution system and a
technical/analytical project.

## Completed / stable

Evidence for these is in application code, Alembic revisions, and
the pytest suite:

- **Core task model** — tree, `active` / `completed` / `cancelled`,
  parent/descendant completion guard, reopen
- **Today planning** — DailyTask queue, planned sessions, ordering,
  carry-forward candidates, unique task+date
- **WorkSession execution / timer** — start, early end, commit
  outcomes, one-running-session invariant, atomic complete/abandon
- **Review / history** — completed sessions by local day, daily
  summary, per-DailyTask performance
- **Business-rule hardening** — title validation, planned-session
  minimums, duration minimums, 409 conflicts vs unexpected errors
- **Timezone handling** — UTC storage, `APP_TIMEZONE` calendar
  interpretation, DST-aware day bounds
- **Current UI** — server-rendered Jinja2 + HTMX for Task List,
  Today, Timer, Review (not claimed as a separately named redesign
  milestone in-repo)
- **Deterministic analytics** — outcomes, dayparts, task_age,
  planning, change, drilldown
- **Production analytical internal boundary** — typed read-only
  packages in `app/analysis/` over approved `app/analytics/`
  helpers
- **Read-only analytical HTTP API** — `GET /api/analysis/*`
  transport over `app/analysis/`, documented in FastAPI `/docs`
- **Analytical API bearer authentication** — machine token from
  `AKRASIA_ANALYSIS_API_TOKEN`; independent of HTML login
- **Human password/session authentication** — password-only login,
  server-side `BrowserSession`, CSRF on cookie-authenticated
  mutations. The CSRF token is rendered into authenticated HTML;
  `akrasia_csrf` is an HttpOnly re-render store, not a validator.
  `POST /login` has no pre-auth CSRF token (documented)
- **Synthetic datasets A–F** — `temporal_patterns`,
  `interruptions_dependencies`, `task_age_abandonment`,
  `planning_workload`, `behaviour_change`, `noise_control`
- **Agent evaluation harness** — generate, evidence, prompt,
  runner, stub/live model interface
- **Frozen evaluation contracts** — `temporal-v1`/`v2`/`v3`,
  `dependencies-v1`, `task-age-v1`, `planning-v1`, `change-v1`

Milestone notes already in the previous README (6A hardening, 6B
settings/logging/identity) remain accurate as completed work.

## Current quality state

Command:

```bash
docker compose --profile test run --rm test
```

Result (this documentation snapshot): **465 passed**, 1 warning
(Starlette `httpx` TestClient deprecation), ~43s.

The suite includes Alembic upgrade/downgrade/upgrade verification
against `system1_test` only.

## Deliberately deferred

Consistent with the current code and README direction:

- Production deployment hardening: HTTPS/reverse proxy, secure
  cookies in production, firewall/network exposure, backups,
  secret provisioning, first VPS
- Multi-user accounts, OAuth, password-reset email, MFA
- Agent memory, embeddings, and tooling
- Wider usability and deployment testing
- Multi-user architecture
- Packaging for nontechnical users

## Near-term next milestones

Architectural, not an implementation plan for this change:

1. Consolidate documentation (done)
2. Production analytical internal boundary (done)
3. Read-only analytical HTTP API (done)
4. Analytical API bearer authentication (done)
5. Human password/session authentication (done)
6. Deploy a first personal instance (HTTPS and secret
   provisioning still outstanding)
7. Dogfood with real personal data
8. Evaluate analytics against real usage before expanding agent
   autonomy

## Current project boundary

Akrasia_Zero is a personal execution system plus a testbed for
honest analytics and bounded model interpretation.

It is not a commercial SaaS product. It is not multi-user. It does
not yet include a production agent.
