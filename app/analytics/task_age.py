"""Task-level execution-age abandonment analysis.

The unit is a Task that has reached a terminal outcome
(``completed`` or ``cancelled``/abandoned), not a WorkSession.

Execution age is local calendar days between the Task's first
qualifying Today appearance and its terminal outcome date.
It is not ``terminal date - Task.created_at``.

First Today appearance
    Earliest ``DailyTask.date`` for the Task whose state is not
    ``removed``. Same-day remove-and-readd restores that row to
    ``planned``, so it still counts. A DailyTask left ``removed``
    is treated as not a genuine lasting commitment.

    Limitation: ``DailyTask.state`` is current state, not an event
    log. Work that happened on a day later marked ``removed`` is
    not recoverable as first Today.

Terminal date
    Completed: latest ``DailyTask.date`` with state ``completed``,
    else the local calendar date of ``Task.completed_at``.
    Cancelled: latest ``DailyTask.date`` with state ``abandoned``,
    else the local date of the latest committed WorkSession with
    outcome ``abandoned``. List-only cancel (no timestamp, no
    abandoned DailyTask/session) cannot be dated and is excluded.

The ``from_date``/``to_date`` window selects Tasks by terminal
outcome date, inclusive, in ``APP_TIMEZONE`` local calendar
dates. First Today may fall before ``from_date``.

Reopened Tasks
    ``reopen_task`` clears ``status``/``completed_at`` and does not
    record a lifecycle event. Active Tasks are excluded. If a Task
    is later terminated again, first Today is still the earliest
    qualifying DailyTask in persisted history. Multiple historical
    terminal cycles cannot be reconstructed.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, Integer, case, cast, func
from sqlalchemy.orm import Session

from app.analytics.types import (
    TaskAgeAnalysis,
    TaskAgeBucket,
    TerminalTaskAge,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession


REMOVED_DAILY_STATE = "removed"
COMPLETED_TASK_STATUS = "completed"
CANCELLED_TASK_STATUS = "cancelled"
COMPLETED_DAILY_STATE = "completed"
ABANDONED_DAILY_STATE = "abandoned"
ABANDONED_SESSION_OUTCOME = "abandoned"
COMPLETED_SESSION_STATE = "completed"
TERMINAL_TASK_STATUSES = (
    COMPLETED_TASK_STATUS,
    CANCELLED_TASK_STATUS,
)


@dataclass(frozen=True)
class ExecutionAgeBucketSpec:
    name: str
    min_days: int
    max_days: int | None


EXECUTION_AGE_BUCKETS: tuple[ExecutionAgeBucketSpec, ...] = (
    ExecutionAgeBucketSpec("0-2", 0, 2),
    ExecutionAgeBucketSpec("3-7", 3, 7),
    ExecutionAgeBucketSpec("8-14", 8, 14),
    ExecutionAgeBucketSpec("15-30", 15, 30),
    ExecutionAgeBucketSpec("31+", 31, None),
)
EXECUTION_AGE_BUCKETS_BY_NAME = {
    spec.name: spec for spec in EXECUTION_AGE_BUCKETS
}


def classify_execution_age_days(days: int) -> str:
    """Assign a local calendar-day age to a production bucket."""
    if days < 0:
        raise ValueError("Execution age in days cannot be negative.")
    for spec in EXECUTION_AGE_BUCKETS:
        if spec.max_days is None:
            if days >= spec.min_days:
                return spec.name
        elif spec.min_days <= days <= spec.max_days:
            return spec.name
    raise ValueError(f"No execution-age bucket for {days} days.")


def task_abandonment_by_execution_age(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> TaskAgeAnalysis:
    """Abandonment rates by execution age for Tasks that terminated
    in the inclusive local-calendar period.
    """
    _require_period(from_date, to_date)
    timezone_name = get_settings().timezone
    terminals = _period_terminal_cte(
        db,
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
    )
    age = cast(
        terminals.c.terminal_date
        - terminals.c.first_today_date,
        Integer,
    )
    bucket = _age_bucket_sql(age)
    rows = (
        db.query(
            bucket.label("age_bucket"),
            func.count().label("terminal_task_count"),
            func.count()
            .filter(
                terminals.c.status == COMPLETED_TASK_STATUS
            )
            .label("completed_count"),
            func.count()
            .filter(
                terminals.c.status == CANCELLED_TASK_STATUS
            )
            .label("abandoned_count"),
        )
        .select_from(terminals)
        .group_by(bucket)
        .order_by(_age_bucket_rank_sql(bucket))
        .all()
    )
    groups = tuple(_bucket_from_row(row) for row in rows)
    return TaskAgeAnalysis(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        total_terminal_tasks=sum(
            group.terminal_task_count for group in groups
        ),
        groups=groups,
    )


def terminal_tasks_with_execution_age(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    limit: int | None = None,
) -> tuple[TerminalTaskAge, ...]:
    """Bounded terminal-Task observations for the period.

    Ordered by terminal date, then task id. Does not include
    titles or unrestricted history.
    """
    _require_period(from_date, to_date)
    timezone_name = get_settings().timezone
    terminals = _period_terminal_cte(
        db,
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
    )
    query = (
        db.query(
            terminals.c.task_id,
            terminals.c.status,
            terminals.c.first_today_date,
            terminals.c.terminal_date,
        )
        .order_by(
            terminals.c.terminal_date,
            terminals.c.task_id,
        )
    )
    if limit is not None:
        query = query.limit(limit)
    return tuple(
        TerminalTaskAge(
            task_id=int(row.task_id),
            first_today_date=row.first_today_date,
            terminal_date=row.terminal_date,
            execution_age_days=(
                row.terminal_date - row.first_today_date
            ).days,
            terminal_outcome=_outcome_label(row.status),
        )
        for row in query.all()
    )


def _require_period(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )


def _local_date_sql(timezone_name: str, timestamp):
    return cast(
        func.timezone(timezone_name, timestamp),
        Date,
    )


def _period_terminal_cte(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    timezone_name: str,
):
    first_today = (
        db.query(
            DailyTask.task_id.label("task_id"),
            func.min(DailyTask.date).label(
                "first_today_date"
            ),
        )
        .filter(DailyTask.state != REMOVED_DAILY_STATE)
        .group_by(DailyTask.task_id)
        .cte("first_today")
    )
    completed_daily = (
        db.query(
            DailyTask.task_id.label("task_id"),
            func.max(DailyTask.date).label(
                "completed_date"
            ),
        )
        .filter(DailyTask.state == COMPLETED_DAILY_STATE)
        .group_by(DailyTask.task_id)
        .cte("completed_daily")
    )
    abandoned_daily = (
        db.query(
            DailyTask.task_id.label("task_id"),
            func.max(DailyTask.date).label(
                "abandoned_date"
            ),
        )
        .filter(DailyTask.state == ABANDONED_DAILY_STATE)
        .group_by(DailyTask.task_id)
        .cte("abandoned_daily")
    )
    abandoned_session = (
        db.query(
            DailyTask.task_id.label("task_id"),
            func.max(
                _local_date_sql(
                    timezone_name,
                    WorkSession.ended_at,
                )
            ).label("abandoned_session_date"),
        )
        .join(
            WorkSession,
            WorkSession.daily_task_id == DailyTask.id,
        )
        .filter(
            WorkSession.session_state
            == COMPLETED_SESSION_STATE,
            WorkSession.outcome == ABANDONED_SESSION_OUTCOME,
            WorkSession.ended_at.isnot(None),
        )
        .group_by(DailyTask.task_id)
        .cte("abandoned_session")
    )
    completed_local_date = _local_date_sql(
        timezone_name,
        Task.completed_at,
    )
    terminal_date = case(
        (
            Task.status == COMPLETED_TASK_STATUS,
            func.coalesce(
                completed_daily.c.completed_date,
                completed_local_date,
            ),
        ),
        (
            Task.status == CANCELLED_TASK_STATUS,
            func.coalesce(
                abandoned_daily.c.abandoned_date,
                abandoned_session.c.abandoned_session_date,
            ),
        ),
    )
    return (
        db.query(
            Task.id.label("task_id"),
            Task.status.label("status"),
            first_today.c.first_today_date,
            terminal_date.label("terminal_date"),
        )
        .join(
            first_today,
            first_today.c.task_id == Task.id,
        )
        .outerjoin(
            completed_daily,
            completed_daily.c.task_id == Task.id,
        )
        .outerjoin(
            abandoned_daily,
            abandoned_daily.c.task_id == Task.id,
        )
        .outerjoin(
            abandoned_session,
            abandoned_session.c.task_id == Task.id,
        )
        .filter(
            Task.status.in_(TERMINAL_TASK_STATUSES),
            terminal_date.isnot(None),
            terminal_date >= from_date,
            terminal_date <= to_date,
            terminal_date >= first_today.c.first_today_date,
        )
        .cte("period_terminal_tasks")
    )


def _age_bucket_sql(age):
    clauses = [
        (age <= spec.max_days, spec.name)
        for spec in EXECUTION_AGE_BUCKETS
        if spec.max_days is not None
    ]
    return case(
        *clauses,
        else_=EXECUTION_AGE_BUCKETS[-1].name,
    )


def _age_bucket_rank_sql(bucket):
    return case(
        {
            spec.name: index
            for index, spec in enumerate(EXECUTION_AGE_BUCKETS)
        },
        value=bucket,
    )


def _outcome_label(status: str) -> str:
    if status == CANCELLED_TASK_STATUS:
        return "abandoned"
    return "completed"


def _bucket_from_row(row) -> TaskAgeBucket:
    spec = EXECUTION_AGE_BUCKETS_BY_NAME[row.age_bucket]
    terminal_task_count = int(row.terminal_task_count)
    completed_count = int(row.completed_count)
    abandoned_count = int(row.abandoned_count)
    if terminal_task_count == 0:
        abandonment_rate = 0.0
        completion_rate = 0.0
    else:
        abandonment_rate = (
            abandoned_count / terminal_task_count
        )
        completion_rate = (
            completed_count / terminal_task_count
        )
    return TaskAgeBucket(
        age_bucket=spec.name,
        min_days=spec.min_days,
        max_days=spec.max_days,
        terminal_task_count=terminal_task_count,
        completed_count=completed_count,
        abandoned_count=abandoned_count,
        abandonment_rate=abandonment_rate,
        completion_rate=completion_rate,
    )
