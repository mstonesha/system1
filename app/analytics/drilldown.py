"""Bounded stuck-Task drill-down.

Returns one row per Task that has at least one committed
WorkSession with outcome ``stuck`` whose local ``started_at``
date falls in the requested inclusive period.

The sample is hard-capped. Order is:

    stuck_session_count DESC
    last_stuck_date DESC
    task_id ASC

so repeated and recently stuck work is inspected first.
This is a deterministic slice, not a random sample and not
an unlimited history dump.

Titles are returned exactly as stored. This module does not
parse prefixes, infer categories, or cluster semantics.
"""

from datetime import date

from sqlalchemy import Date, cast, desc, func
from sqlalchemy.orm import Session

from app.analytics.types import StuckTaskDrilldown, StuckTaskObservation
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from app.time import local_day_bounds_utc


COMPLETED_SESSION_STATE = "completed"
STUCK_OUTCOME = "stuck"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 50


def stuck_task_drilldown(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    limit: int = DEFAULT_LIMIT,
) -> StuckTaskDrilldown:
    """Bounded Tasks associated with stuck sessions in a local period."""
    _require_period(from_date, to_date)
    _require_limit(limit)
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    stuck_filters = (
        WorkSession.started_at >= start_utc,
        WorkSession.started_at < end_utc,
        WorkSession.session_state == COMPLETED_SESSION_STATE,
        WorkSession.outcome == STUCK_OUTCOME,
    )
    totals = (
        db.query(
            func.count().label("stuck_sessions"),
            func.count(func.distinct(DailyTask.task_id)).label(
                "stuck_tasks"
            ),
        )
        .select_from(WorkSession)
        .join(
            DailyTask,
            DailyTask.id == WorkSession.daily_task_id,
        )
        .filter(*stuck_filters)
        .one()
    )
    local_date = cast(
        func.timezone(timezone_name, WorkSession.started_at),
        Date,
    )
    stuck_count = func.count()
    last_stuck = func.max(local_date)
    rows = (
        db.query(
            Task.id.label("task_id"),
            Task.title.label("title"),
            Task.status.label("task_status"),
            stuck_count.label("stuck_session_count"),
            func.min(local_date).label("first_stuck_date"),
            last_stuck.label("last_stuck_date"),
        )
        .select_from(WorkSession)
        .join(
            DailyTask,
            DailyTask.id == WorkSession.daily_task_id,
        )
        .join(Task, Task.id == DailyTask.task_id)
        .filter(*stuck_filters)
        .group_by(Task.id, Task.title, Task.status)
        .order_by(
            desc(stuck_count),
            desc(last_stuck),
            Task.id.asc(),
        )
        .limit(limit)
        .all()
    )
    tasks = tuple(_observation(row) for row in rows)
    return StuckTaskDrilldown(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        total_stuck_sessions=int(totals.stuck_sessions),
        total_distinct_stuck_tasks=int(totals.stuck_tasks),
        returned_task_count=len(tasks),
        limit=limit,
        tasks=tasks,
    )


def _require_period(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )


def _require_limit(limit: int) -> None:
    if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise ValueError(
            f"limit must be an integer from {MIN_LIMIT} "
            f"to {MAX_LIMIT}."
        )


def _period_bounds(from_date: date, to_date: date) -> tuple:
    start_utc, _ = local_day_bounds_utc(from_date)
    _, end_utc = local_day_bounds_utc(to_date)
    return start_utc, end_utc, get_settings().timezone


def _observation(row) -> StuckTaskObservation:
    return StuckTaskObservation(
        task_id=int(row.task_id),
        title=row.title,
        stuck_session_count=int(row.stuck_session_count),
        first_stuck_date=row.first_stuck_date,
        last_stuck_date=row.last_stuck_date,
        task_status=row.task_status,
    )
