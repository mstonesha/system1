"""Planning accuracy, effort estimation, and weekly workload.

Three related families. They are not collapsed into one score.

Capacity (DailyTask)
    Grain: one DailyTask whose local ``date`` falls in the
    requested inclusive period. Currently ``removed`` rows are
    excluded; current state is not an event log.

    ``planned_sessions IS NULL`` is not zero. Those rows are
    counted separately and omitted from planned-vs-actual
    comparison totals.

    Unused explicit planned sessions:

    - DailyTask.state completed → unused_due_to_early_completion
    - DailyTask.state abandoned → unused_on_abandonment
    - otherwise → unused_while_unfinished

Effort (completed Task)
    Grain: one completed Task with ``estimated_sessions > 0``
    whose completion date (latest completed DailyTask.date, else
    local ``Task.completed_at``) falls in the period.

    Actual effort is lifetime committed WorkSessions across all
    non-removed DailyTasks for the Task, including sessions
    before ``from_date``.

    Near estimate means actual == estimated (integer session
    counts). Reopened Tasks use the current completed lifecycle
    only.

Workload (local ISO week)
    Grain: one Monday-start ISO week that has DailyTasks or
    committed sessions inside the requested local dates.

    Partial boundary weeks are included; ``week_start_date`` may
    fall before ``from_date``. Contributions are not expanded to
    the rest of that ISO week.
"""

from datetime import date

from sqlalchemy import Date, Float, Integer, case, cast, extract, func
from sqlalchemy.orm import Session

from app.analytics.types import (
    COMMITTED_OUTCOMES,
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    DailyPlanningSummary,
    TaskEffortEstimation,
    WeeklyWorkloadAnalysis,
    WeeklyWorkloadGroup,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession


REMOVED_DAILY_STATE = "removed"
COMPLETED_DAILY_STATE = "completed"
ABANDONED_DAILY_STATE = "abandoned"
COMPLETED_TASK_STATUS = "completed"
COMPLETED_SESSION_STATE = "completed"


def daily_planning_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> DailyPlanningSummary:
    """Planned versus actual sessions on DailyTasks in the period."""
    _require_period(from_date, to_date)
    timezone_name = get_settings().timezone
    actual = _actual_by_daily_cte(db)
    actual_count = func.coalesce(actual.c.actual_sessions, 0)
    unused = func.greatest(
        DailyTask.planned_sessions - actual_count,
        0,
    )
    has_plan = DailyTask.planned_sessions.isnot(None)
    row = (
        db.query(
            func.count()
            .filter(has_plan)
            .label("with_plan"),
            func.count()
            .filter(DailyTask.planned_sessions.is_(None))
            .label("without_plan"),
            func.coalesce(
                func.sum(DailyTask.planned_sessions),
                0,
            ).label("planned"),
            func.coalesce(
                func.sum(actual_count).filter(has_plan),
                0,
            ).label("actual"),
            func.coalesce(
                func.sum(unused).filter(has_plan),
                0,
            ).label("unused"),
            func.coalesce(
                func.sum(unused).filter(
                    has_plan,
                    DailyTask.state == COMPLETED_DAILY_STATE,
                ),
                0,
            ).label("unused_early"),
            func.coalesce(
                func.sum(unused).filter(
                    has_plan,
                    DailyTask.state == ABANDONED_DAILY_STATE,
                ),
                0,
            ).label("unused_abandoned"),
            func.coalesce(
                func.sum(unused).filter(
                    has_plan,
                    DailyTask.state != COMPLETED_DAILY_STATE,
                    DailyTask.state != ABANDONED_DAILY_STATE,
                ),
                0,
            ).label("unused_unfinished"),
            func.count().filter(
                has_plan,
                actual_count < DailyTask.planned_sessions,
            ).label("below_plan"),
            func.count().filter(
                has_plan,
                actual_count == DailyTask.planned_sessions,
            ).label("equal_plan"),
            func.count().filter(
                has_plan,
                actual_count > DailyTask.planned_sessions,
            ).label("above_plan"),
        )
        .select_from(DailyTask)
        .outerjoin(
            actual,
            actual.c.daily_task_id == DailyTask.id,
        )
        .filter(
            DailyTask.date >= from_date,
            DailyTask.date <= to_date,
            DailyTask.state != REMOVED_DAILY_STATE,
        )
        .one()
    )
    planned = int(row.planned)
    actual_total = int(row.actual)
    return DailyPlanningSummary(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        daily_tasks_with_explicit_plan=int(row.with_plan),
        daily_tasks_without_explicit_plan=int(
            row.without_plan
        ),
        total_planned_sessions=planned,
        total_actual_sessions=actual_total,
        execution_ratio=(
            actual_total / planned if planned else None
        ),
        total_unused_planned_sessions=int(row.unused),
        unused_due_to_early_completion=int(row.unused_early),
        unused_while_unfinished=int(row.unused_unfinished),
        unused_on_abandonment=int(row.unused_abandoned),
        daily_tasks_actual_below_plan=int(row.below_plan),
        daily_tasks_actual_equal_plan=int(row.equal_plan),
        daily_tasks_actual_above_plan=int(row.above_plan),
    )


def task_effort_estimation(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> TaskEffortEstimation:
    """Estimated versus lifetime actual sessions for completed Tasks."""
    _require_period(from_date, to_date)
    timezone_name = get_settings().timezone
    lifetime = _lifetime_actual_cte(db)
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
    completed_local = cast(
        func.timezone(timezone_name, Task.completed_at),
        Date,
    )
    completion_date = func.coalesce(
        completed_daily.c.completed_date,
        completed_local,
    )
    ratio = (
        cast(lifetime.c.actual_sessions, Float)
        / cast(Task.estimated_sessions, Float)
    )
    row = (
        db.query(
            func.count().label("n"),
            func.avg(Task.estimated_sessions).label(
                "mean_estimated"
            ),
            func.avg(lifetime.c.actual_sessions).label(
                "mean_actual"
            ),
            func.avg(ratio).label("mean_ratio"),
            func.percentile_cont(0.5)
            .within_group(ratio)
            .label("median_ratio"),
            func.count()
            .filter(
                lifetime.c.actual_sessions
                < Task.estimated_sessions
            )
            .label("below_count"),
            func.count()
            .filter(
                lifetime.c.actual_sessions
                == Task.estimated_sessions
            )
            .label("near_count"),
            func.count()
            .filter(
                lifetime.c.actual_sessions
                > Task.estimated_sessions
            )
            .label("above_count"),
        )
        .select_from(Task)
        .join(
            lifetime,
            lifetime.c.task_id == Task.id,
        )
        .outerjoin(
            completed_daily,
            completed_daily.c.task_id == Task.id,
        )
        .filter(
            Task.status == COMPLETED_TASK_STATUS,
            Task.estimated_sessions > 0,
            completion_date >= from_date,
            completion_date <= to_date,
        )
        .one()
    )
    n = int(row.n)
    if n == 0:
        return TaskEffortEstimation(
            from_date=from_date,
            to_date=to_date,
            timezone=timezone_name,
            completed_tasks_with_estimate=0,
            mean_estimated_sessions=None,
            mean_actual_sessions=None,
            mean_actual_to_estimated_ratio=None,
            median_actual_to_estimated_ratio=None,
            below_estimate_count=0,
            near_estimate_count=0,
            above_estimate_count=0,
        )
    return TaskEffortEstimation(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        completed_tasks_with_estimate=n,
        mean_estimated_sessions=float(row.mean_estimated),
        mean_actual_sessions=float(row.mean_actual),
        mean_actual_to_estimated_ratio=float(row.mean_ratio),
        median_actual_to_estimated_ratio=float(
            row.median_ratio
        ),
        below_estimate_count=int(row.below_count),
        near_estimate_count=int(row.near_count),
        above_estimate_count=int(row.above_count),
    )


def weekly_workload(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> WeeklyWorkloadAnalysis:
    """ISO-week planned load and committed session outcomes."""
    _require_period(from_date, to_date)
    timezone_name = get_settings().timezone
    daily_week = _iso_week_start_sql(DailyTask.date)
    daily_cte = (
        db.query(
            daily_week.label("week_start"),
            func.count().label("daily_task_count"),
            func.count()
            .filter(DailyTask.planned_sessions.is_(None))
            .label("without_plan"),
            func.coalesce(
                func.sum(DailyTask.planned_sessions),
                0,
            ).label("planned_sessions"),
        )
        .filter(
            DailyTask.date >= from_date,
            DailyTask.date <= to_date,
            DailyTask.state != REMOVED_DAILY_STATE,
        )
        .group_by(daily_week)
        .cte("daily_weeks")
    )
    local_date = cast(
        func.timezone(timezone_name, WorkSession.started_at),
        Date,
    )
    session_week = _iso_week_start_sql(local_date)
    session_cte = (
        db.query(
            session_week.label("week_start"),
            func.count().label("actual_sessions"),
            func.count()
            .filter(
                WorkSession.outcome.in_(POSITIVE_OUTCOMES)
            )
            .label("positive_sessions"),
            func.count()
            .filter(
                WorkSession.outcome.in_(NEGATIVE_OUTCOMES)
            )
            .label("negative_sessions"),
        )
        .join(
            DailyTask,
            DailyTask.id == WorkSession.daily_task_id,
        )
        .filter(
            local_date >= from_date,
            local_date <= to_date,
            DailyTask.state != REMOVED_DAILY_STATE,
            *_committed_session_filters(),
        )
        .group_by(session_week)
        .cte("session_weeks")
    )
    week_start = func.coalesce(
        daily_cte.c.week_start,
        session_cte.c.week_start,
    )
    rows = (
        db.query(
            week_start.label("week_start_date"),
            func.coalesce(
                daily_cte.c.daily_task_count,
                0,
            ).label("daily_task_count"),
            func.coalesce(
                daily_cte.c.without_plan,
                0,
            ).label("without_plan"),
            func.coalesce(
                daily_cte.c.planned_sessions,
                0,
            ).label("planned_sessions"),
            func.coalesce(
                session_cte.c.actual_sessions,
                0,
            ).label("actual_sessions"),
            func.coalesce(
                session_cte.c.positive_sessions,
                0,
            ).label("positive_sessions"),
            func.coalesce(
                session_cte.c.negative_sessions,
                0,
            ).label("negative_sessions"),
        )
        .select_from(
            daily_cte.join(
                session_cte,
                daily_cte.c.week_start
                == session_cte.c.week_start,
                full=True,
            )
        )
        .order_by(week_start)
        .all()
    )
    groups = tuple(
        _weekly_group(row) for row in rows
    )
    return WeeklyWorkloadAnalysis(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        groups=groups,
    )


def _require_period(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )


def _committed_session_filters():
    return (
        WorkSession.session_state == COMPLETED_SESSION_STATE,
        WorkSession.outcome.in_(COMMITTED_OUTCOMES),
    )


def _actual_by_daily_cte(db: Session):
    return (
        db.query(
            WorkSession.daily_task_id.label("daily_task_id"),
            func.count().label("actual_sessions"),
        )
        .filter(*_committed_session_filters())
        .group_by(WorkSession.daily_task_id)
        .cte("actual_by_daily")
    )


def _lifetime_actual_cte(db: Session):
    return (
        db.query(
            DailyTask.task_id.label("task_id"),
            func.count().label("actual_sessions"),
        )
        .join(
            WorkSession,
            WorkSession.daily_task_id == DailyTask.id,
        )
        .filter(
            DailyTask.state != REMOVED_DAILY_STATE,
            *_committed_session_filters(),
        )
        .group_by(DailyTask.task_id)
        .cte("lifetime_actual")
    )


def _iso_week_start_sql(date_expr):
    """Monday date of the ISO week containing ``date_expr``."""
    isodow = cast(extract("isodow", date_expr), Integer)
    return date_expr - (isodow - 1)


def _weekly_group(row) -> WeeklyWorkloadGroup:
    actual = int(row.actual_sessions)
    positive = int(row.positive_sessions)
    negative = int(row.negative_sessions)
    if actual == 0:
        positive_rate = None
        negative_rate = None
    else:
        positive_rate = positive / actual
        negative_rate = negative / actual
    return WeeklyWorkloadGroup(
        week_start_date=row.week_start_date,
        daily_task_count=int(row.daily_task_count),
        daily_tasks_without_explicit_plan=int(
            row.without_plan
        ),
        planned_sessions=int(row.planned_sessions),
        actual_sessions=actual,
        positive_sessions=positive,
        negative_sessions=negative,
        positive_rate=positive_rate,
        negative_rate=negative_rate,
    )
