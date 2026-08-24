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

Result (this documentation snapshot): **358 passed**, 1 warning
(Starlette `httpx` TestClient deprecation), ~38s.

The suite includes Alembic upgrade/downgrade/upgrade verification
against `system1_test` only.

## Deliberately deferred

Consistent with the current code and README direction:

- Production deployment hardening
- Authentication (none is implemented)
- HTTPS / reverse-proxy configuration
- Backups and recovery
- Production Enkrateia_One agent
- Production API boundary for agents
- Agent memory, embeddings, and tooling
- Wider usability and deployment testing
- Multi-user architecture
- Packaging for nontechnical users

## Near-term next milestones

Architectural, not an implementation plan for this change:

1. Consolidate documentation (this snapshot)
2. Define a production analytical-agent / API boundary
3. Deployment and security hardening
4. Deploy a first personal instance
5. Dogfood with real personal data
6. Evaluate analytics against real usage before expanding agent
   autonomy

None of these is implemented in this documentation change.

## Current project boundary

Akrasia_Zero is a personal execution system plus a testbed for
honest analytics and bounded model interpretation.

It is not a commercial SaaS product. It is not multi-user. It does
not yet include a production agent.
