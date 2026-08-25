# Architecture

Akrasia_Zero is a server-rendered personal execution system. Business
rules live in Python services. PostgreSQL stores the operational
record. Deterministic analytics sit beside the application as a
calculation library. Approved production callers go through
`app/analysis/`. Model interpretation, when it happens at all,
happens only in the isolated evaluation harness, downstream of a
bounded evidence contract.

Enkrateia_One is the name reserved for a future analytical/agent
layer. It is not an implemented production subsystem.

## System overview

Current stack: FastAPI, Jinja2, HTMX, PostgreSQL 17, SQLAlchemy 2.x,
Alembic, Docker Compose, pytest. Uvicorn serves the app. Small
client-side scripts (`app/static/timer.js`, `app/static/break.js`)
drive the timer and break countdown.

Approximate flow:

```
Browser
  ↓
FastAPI routes (`app/routes/`)
  ↓
service / domain logic (`app/services/`)
  ↓
SQLAlchemy (`app/models.py`)
  ↓
PostgreSQL

trusted machine client
  ↓
Bearer authentication
  ↓
/api/analysis/*
  ↓
app/analysis/
  ↓
app/analytics/

evaluation path (harness only):
deterministic analytics (`app/analytics/`)
  ↓
bounded evidence contract (`evaluation/agent/`)
  ↓
analytical-agent evaluation
```

The production contract is served as a local read-only JSON API
at `/api/analysis/*`. That API uses possession-based machine
bearer authentication (`AKRASIA_ANALYSIS_API_TOKEN`). It is not
human/browser login. The HTML UI remains unauthenticated. Do not
expose the application on the public internet. Enkrateia_One is
not implemented. The HTML UI still does not call analytics.
Evaluation still calls production analytics directly and is
unchanged.

## Domain model

Three entities, defined in `app/models.py`.

### Task

Durable item in a tree. Important fields: `title`, `status`
(default `active`), optional `parent_task_id`, optional
`estimated_sessions`, `completed_at`, `sort_order`.

Statuses used by services: `active`, `completed`, `cancelled`.
`complete_task` refuses to complete a Task that still has active
descendants (`has_active_descendants`). `cancel_task` sets
`cancelled` and clears `completed_at`. `reopen_task` sets `active`
and clears `completed_at`. Reopen does not write a lifecycle event,
so later analytics cannot reconstruct multiple terminal cycles.

### DailyTask

A Task queued on a specific local calendar `date`, with optional
`planned_sessions` and queue `sort_order`. Unique on
`(task_id, date)` (`uq_daily_task_task_date`).

`state` defaults to `planned`. Services also use `removed`,
`completed`, and `abandoned`. The executable Today queue is
DailyTasks that are still `planned` whose Task is `active`.
Historical rows are kept; they drop out of the queue.

`planned_sessions is NULL` means planning was not explicit. That is
not treated as zero in analytics.

Removing a DailyTask with a running WorkSession is refused. A
`removed` row can be restored to `planned` if the Task is added
again on the same date.

DailyTask is not Task. Task is the durable identity; DailyTask is a
dated queue row plus that day’s planned capacity.

### WorkSession

One timed focus attempt against a DailyTask. `session_state`
defaults to `running`. Commit sets `completed`. Allowed outcomes in
`app/services/sessions.py`: `progress`, `complete`, `stuck`,
`paused`, `abandoned`. `interrupted` is a boolean, not an outcome.

At most one WorkSession may be `running`. Enforced by a partial
unique index (`uq_work_sessions_one_running` where
`session_state = 'running'`) and by application checks. A concurrent
second start is rolled back.

Commit that completes or abandons a task updates Task, DailyTask,
and WorkSession in one transaction. `complete` marks the Task
completed and the DailyTask `completed`. `abandoned` cancels the
Task and sets the DailyTask to `abandoned`. `stuck` and `paused`
move the DailyTask to the bottom of the day’s queue. Failure after
those writes rolls all of it back.

A DailyTask with a running session cannot be removed from the day.

## Business-logic boundary

| Layer | Responsibility |
|---|---|
| `app/routes/` | HTTP, forms, template context. Thin. |
| `app/routes/analysis.py` | Read-only JSON transport over `app/analysis/`. Machine bearer auth. |
| `app/api/auth.py` | Shared FastAPI bearer-token dependency for that JSON API |
| `app/api/` | Pydantic JSON views of `app/analysis/` contracts. Not imported by analysis. |
| `app/services/` | Domain rules: task lifecycle, Today queue, session timer, review summaries |
| `app/models.py` | Persistence mapping |
| `app/analytics/` | Deterministic aggregates. No recommendations, no significance tests, no “best day” fields |
| `app/analysis/` | Approved read-only analytical packages over `app/analytics/`. No interpretation, no SQL, no writes |

Routes should not embed business rules. Analytics and the
production analytical contract must not import `evaluation`.
Evaluation may call production analytics; it must not write to the
application database. Analytical HTTP handlers call
`app/analysis/`, not arbitrary analytics helpers.

## Time semantics

Persisted timestamps are timezone-aware UTC (`utc_now()` in
`app/time.py`). Calendar interpretation uses `APP_TIMEZONE`
(default `Europe/London`, DST-aware).

`today()` is the current date in that zone. `local_day_bounds_utc`
returns a half-open UTC interval `[start, end)` covering one local
calendar date. Review and session-outcome analytics use that
interval on `WorkSession.started_at` (or the equivalent local-date
filter documented in each module).

Inclusive local `from_date`/`to_date` windows in analytics are
converted to those UTC bounds. ISO weeks are Monday-start. Partial
boundary weeks are included and not expanded to a full week.

## Analytics architecture

`app/analytics/` is a calculation layer. It produces counts, rates,
and bounded drilldowns. It does not interpret them.

Session-outcome families use committed sessions only
(`session_state = 'completed'` and outcomes in
`progress` / `complete` / `stuck` / `paused` / `abandoned`).
Positive outcomes are `progress` and `complete`. Negative outcomes
are `stuck`, `paused`, and `abandoned`. Rates are unrounded
proportions in `[0, 1]`.

Current modules:

- **outcomes** (`session_outcomes_by_weekday`,
  `session_outcomes_by_daypart`,
  `session_outcomes_by_weekday_daypart`,
  `session_outcomes_by_interruption`)
- **dayparts** (`classify_daypart`, `DAYPARTS`): overnight
  00:00–05:59, early_morning 06:00–08:59, morning 09:00–11:59,
  early_afternoon 12:00–14:59, late_afternoon 15:00–17:59, evening
  18:00 onward (half-open local-time intervals)
- **task_age** (`task_abandonment_by_execution_age`,
  `terminal_tasks_with_execution_age`,
  `classify_execution_age_days`): execution age is local calendar
  days from the first non-`removed` DailyTask date to the terminal
  date, not `Task.created_at`
- **planning** (`daily_planning_summary`, `task_effort_estimation`,
  `weekly_workload`): capacity, completed-task effort, and weekly
  planned load are separate families
- **change** (`morning_afternoon_window`,
  `weekly_morning_afternoon_outcomes`,
  `compare_morning_afternoon_windows`, `rolling_window_dates`):
  morning 09:00–11:59 vs afternoon 12:00–17:59; callers choose
  windows
- **drilldown** (`stuck_task_drilldown`): hard-capped sample
  (default 12, max 50), ordered by stuck count then recency. Titles
  are passed through unparsed

Callers cannot supply arbitrary SQL. Grouping keys are interruption
status or local weekday/daypart derived from `started_at` in
`APP_TIMEZONE`.

## Production analytical contract

`app/analysis/` is the approved production question set. It may
call and combine `app/analytics/` helpers. It does not add
recommendations, hypotheses, prompts, or model I/O.

Public methods: `get_temporal_summary`,
`get_interruption_summary`, `get_task_age_summary`,
`get_planning_summary`, `get_change_summary`,
`get_stuck_task_drilldown`, and bounded
`get_terminal_task_age_drilldown`.

Inclusive local `from_date`/`to_date` windows are rejected when
reversed. Date-range size is not capped. Stuck and terminal-age
drilldowns use the production stuck cap (default 12, min 1,
max 50). The underlying `terminal_tasks_with_execution_age`
helper can omit `limit`; the contract never does.

Change windows use a stable preset: full requested period,
recent 8 weeks, recent 16 weeks, preceding comparable 16 weeks,
recent-16 versus preceding-16, and the weekly morning/afternoon
series. Window lengths are not caller-chosen.

This layer accepts an existing SQLAlchemy session. It does not
open engines, mutate Task / DailyTask / WorkSession, or expose
arbitrary SQL.

Dependency direction is `app/analysis` → `app/analytics`.
Analytics must not import `app/analysis`. HTTP transport is
`app/routes/analysis.py` → `app/analysis`. Machine clients
authenticate with a bearer token; `app/analysis/` itself is
unaware of FastAPI security.

## Agent boundary

Architectural principle:

```
PostgreSQL truth
  → deterministic analytics (`app/analytics/`)
  → bounded typed analytical contract (`app/analysis/`)
  → read-only HTTP API (`/api/analysis/*`, machine bearer auth)
  → future interpretation (Enkrateia_One, not implemented)

evaluation remains:
  analytics → evidence contract → model interpretation
```

Consequences:

- The analytical model does not receive arbitrary SQL access
- The model does not talk to the database
- Deterministic business rules stay in software
- Agent conclusions are downstream of bounded evidence
- The current agent system is an evaluation harness, not the
  production Enkrateia_One layer

Evidence builders in `evaluation/agent/evidence.py` call production
analytics only. They do not load ground truth, name scenarios, or
narrate measurements. The model sees an opaque `case_id`
(`case_a` … `case_f`), not scenario names.

## Akrasia_Zero / Enkrateia_One

**Akrasia_Zero** is the operational execution/task system: capture
work, run sessions, keep an honest queue, retain history.

**Enkrateia_One** is the intended future analytical/agent layer.
Agents should use a bounded API, not connect to Postgres. The
internal Python contract for that API exists in `app/analysis/`.
A local HTTP transport is implemented at `/api/analysis/*` and
protected with a machine bearer token. That is API authentication
for trusted callers, not user/browser authentication. It must not
be treated as public-internet readiness. Enkrateia_One is not
present as a package, service, or runtime.

## Data-history philosophy

Historical rows are retained where the current model supports them.
Analytics must not silently rewrite history. Known limitations of
the current schema:

- **Reopened tasks:** `reopen_task` clears `status` and
  `completed_at` without an event log. Active tasks are excluded
  from terminal-age analysis. A later termination still uses the
  earliest qualifying DailyTask as first Today.
- **Terminal dating:** completed tasks use the latest completed
  DailyTask date, else the local date of `Task.completed_at`.
  Cancelled tasks use the latest abandoned DailyTask date, else the
  latest committed abandoned WorkSession. List-only cancellation
  (no timestamp, no abandoned DailyTask/session) cannot be dated
  and is excluded from execution-age packages.
- **DailyTask current state:** `DailyTask.state` is current state,
  not an event log. A day later marked `removed` is not recoverable
  as first Today. Unused planned capacity is classified from
  current state (unfinished / early completion / abandonment), not
  from why the gap first appeared.
- **Planning nulls:** `planned_sessions is NULL` is omitted from
  planned-versus-actual totals.

## Non-goals / current exclusions

Deliberately out of scope for this repository state:

- Arbitrary SQL agents
- Autonomous task execution
- AI-owned business rules
- Unrestricted database access
- Multi-user SaaS architecture
- Production agent memory, embeddings, or tool loops
- Human/browser login, accounts, roles, or session cookies
- Production reverse-proxy / HTTPS configuration
- Backups and recovery automation
