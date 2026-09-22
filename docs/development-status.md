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
  Today, Timer, Review. HTMX is progressive enhancement, not an
  SPA. Phone work is responsive CSS, not a second application
- **M10A Daily Plan interaction** — complete. M10A.1 layout,
  sticky header, and long-title/clarity refinements. M10A.2
  targeted HTMX for Move Up/Down, Save/Update Plan, and
  Add/Remove from Day, with normal POST/303 fallback; Timer,
  date navigation, auth, and top-level nav remain full-page.
  M10A.3 desktop/mouse drag-and-drop queue ordering; the
  server validates a complete permutation and stays
  authoritative for `sort_order`; Up/Down remain the
  keyboard/touch/no-JS fallback
- **M10B responsive/mobile usability** — complete. M10B.1 Daily
  Plan stacks at 900px (was 1100px), queue before source tree,
  session controls stack when narrow, drag handle hidden for
  coarse pointers. M10B.2 compact phone shell, one-row primary
  nav, reduced identity/padding/min-heights, no sticky mobile
  nav. M10B.3 tighter Task List indent at ≤480px; hierarchy and
  44px targets kept. M10B.4 Review tables still pan
  horizontally; the task column is sticky at ≤480px; no
  columns hidden and no card duplicate
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
- **Production deployment configuration** — standalone
  `docker-compose.prod.yml`, Caddy TLS termination, Hostinger
  Traefik overlay (Caddy disabled; existing Traefik owns 80/443),
  health endpoint, named volume, backup/restore scripts. First VPS
  is not provisioned from this repository
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

Result (this documentation snapshot): **578 passed**, 2 warnings
(Starlette `httpx` TestClient deprecation; `anyio.abc.BlockingPortal`
alias deprecation), ~120s. Those warnings are known and not
treated as fixed.

The suite includes Alembic upgrade/downgrade/upgrade verification
against `system1_test` only.

## Deliberately deferred

Consistent with the current code and README direction:

- Multi-user accounts, OAuth, password-reset email, MFA
- Agent memory, embeddings, and tooling
- Actual VPS provisioning, Tailscale, Hermes, n8n
- Wider usability and deployment testing
- Multi-user architecture
- Packaging for nontechnical users

## Near-term direction

Architectural, not an implementation plan. M10A and M10B are
complete. No further M10 slice is queued; the next
implementation work is open pending a deliberate decision.

1. Consolidate documentation (done)
2. Production analytical internal boundary (done)
3. Read-only analytical HTTP API (done)
4. Analytical API bearer authentication (done)
5. Human password/session authentication (done)
6. Production Compose / Caddy / backup readiness (done;
   Hostinger Traefik overlay added; first VPS still operator work)
7. Deploy a first personal instance
8. Use Akrasia seriously in day-to-day work; identify real
   friction before adding features
9. Evaluate analytics against real usage before expanding agent
   autonomy

Hermes / Enkrateia_One remain a future possibility. Agent
access stays through bounded analytical interfaces; do not rush
agent integration. Deployment and operational improvements
remain valid where already documented.

## Current project boundary

Akrasia_Zero is a personal execution system plus a testbed for
honest analytics and bounded model interpretation.

It is not a commercial SaaS product. It is not multi-user. It does
not yet include a production agent.
