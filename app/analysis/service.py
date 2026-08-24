"""Production analytical contract.

Assembles approved deterministic analytics into typed
packages. Callers pass an existing SQLAlchemy session.
This module does not open connections, write rows, or
interpret measurements.

Approved questions:

- ``get_temporal_summary``
- ``get_interruption_summary``
- ``get_task_age_summary``
- ``get_planning_summary``
- ``get_change_summary``
- ``get_stuck_task_drilldown``
- ``get_terminal_task_age_drilldown``

Change windows use a stable preset matching the evaluated
design: full requested period, recent 8 weeks, recent 16
weeks, preceding comparable 16 weeks, recent-16 versus
preceding-16, and the weekly morning/afternoon series.
Window lengths are not caller-chosen.

Date-range size is not capped here. Inclusive local dates
are rejected only when ``from_date`` is after ``to_date``.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.analysis.types import (
    AnalysisPeriod,
    ChangeSummary,
    InterruptionSummary,
    PlanningSummary,
    StuckTaskSummary,
    TaskAgeSummary,
    TemporalSummary,
    TerminalTaskAgeDrilldown,
)
from app.analytics.change import (
    compare_morning_afternoon_windows,
    morning_afternoon_window,
    rolling_window_dates,
    weekly_morning_afternoon_outcomes,
)
from app.analytics.drilldown import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    stuck_task_drilldown,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_interruption,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)
from app.analytics.planning import (
    daily_planning_summary,
    task_effort_estimation,
    weekly_workload,
)
from app.analytics.task_age import (
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.config import get_settings


def get_temporal_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> TemporalSummary:
    """Weekday, daypart, and morning/afternoon session outcomes."""
    period = _period(from_date, to_date)
    weekday = session_outcomes_by_weekday(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    daypart = session_outcomes_by_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    cells = session_outcomes_by_weekday_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    morning_afternoon = morning_afternoon_window(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return TemporalSummary(
        period=period,
        total_sessions=weekday.total_sessions,
        by_weekday=weekday.groups,
        by_daypart=daypart.groups,
        by_weekday_daypart=cells.groups,
        morning_afternoon=morning_afternoon,
    )


def get_interruption_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> InterruptionSummary:
    """Committed outcomes split by interruption status."""
    period = _period(from_date, to_date)
    result = session_outcomes_by_interruption(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return InterruptionSummary(
        period=period,
        total_sessions=result.total_sessions,
        groups=result.groups,
    )


def get_task_age_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> TaskAgeSummary:
    """Execution-age abandonment buckets for terminal Tasks.

    Does not include per-task rows. Use
    ``get_terminal_task_age_drilldown`` for a bounded
    task-level sample.
    """
    period = _period(from_date, to_date)
    result = task_abandonment_by_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return TaskAgeSummary(
        period=period,
        total_terminal_tasks=result.total_terminal_tasks,
        buckets=result.groups,
    )


def get_terminal_task_age_drilldown(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    limit: int = DEFAULT_LIMIT,
) -> TerminalTaskAgeDrilldown:
    """Bounded terminal-Task execution-age rows.

    The production helper allows an omitted (unbounded)
    limit. This contract always supplies a hard cap.
    """
    period = _period(from_date, to_date)
    _require_limit(limit)
    tasks = terminal_tasks_with_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return TerminalTaskAgeDrilldown(
        period=period,
        limit=limit,
        returned_task_count=len(tasks),
        tasks=tasks,
    )


def get_planning_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> PlanningSummary:
    """Daily capacity, task effort, and weekly workload.

    The three subfamilies stay separate. This package does
    not compute a planning score or overload flag.
    """
    period = _period(from_date, to_date)
    return PlanningSummary(
        period=period,
        daily_planning=daily_planning_summary(
            db,
            from_date=from_date,
            to_date=to_date,
        ),
        task_effort=task_effort_estimation(
            db,
            from_date=from_date,
            to_date=to_date,
        ),
        weekly_workload=weekly_workload(
            db,
            from_date=from_date,
            to_date=to_date,
        ),
    )


def get_change_summary(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> ChangeSummary:
    """Morning/afternoon windows and the weekly series.

    Rolling windows are anchored on ``to_date`` and may
    start before ``from_date``. This method does not label
    regimes or detect change-points.
    """
    period = _period(from_date, to_date)
    recent_8w_from, recent_8w_to = rolling_window_dates(
        to_date,
        weeks=8,
    )
    recent_16w_from, recent_16w_to = rolling_window_dates(
        to_date,
        weeks=16,
    )
    preceding_16w_from, preceding_16w_to = (
        rolling_window_dates(
            recent_16w_from - timedelta(days=1),
            weeks=16,
        )
    )
    return ChangeSummary(
        period=period,
        full_period=morning_afternoon_window(
            db,
            from_date=from_date,
            to_date=to_date,
        ),
        recent_8w=morning_afternoon_window(
            db,
            from_date=recent_8w_from,
            to_date=recent_8w_to,
        ),
        recent_16w=morning_afternoon_window(
            db,
            from_date=recent_16w_from,
            to_date=recent_16w_to,
        ),
        preceding_16w=morning_afternoon_window(
            db,
            from_date=preceding_16w_from,
            to_date=preceding_16w_to,
        ),
        recent_16w_vs_preceding_16w=(
            compare_morning_afternoon_windows(
                db,
                current_from_date=recent_16w_from,
                current_to_date=recent_16w_to,
                baseline_from_date=preceding_16w_from,
                baseline_to_date=preceding_16w_to,
            )
        ),
        weekly=weekly_morning_afternoon_outcomes(
            db,
            from_date=from_date,
            to_date=to_date,
        ),
    )


def get_stuck_task_drilldown(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    limit: int = DEFAULT_LIMIT,
) -> StuckTaskSummary:
    """Bounded Tasks with stuck sessions in the period."""
    period = _period(from_date, to_date)
    result = stuck_task_drilldown(
        db,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return StuckTaskSummary(
        period=period,
        total_stuck_sessions=result.total_stuck_sessions,
        total_distinct_stuck_tasks=(
            result.total_distinct_stuck_tasks
        ),
        returned_task_count=result.returned_task_count,
        limit=result.limit,
        tasks=result.tasks,
    )


def _period(from_date: date, to_date: date) -> AnalysisPeriod:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )
    return AnalysisPeriod(
        from_date=from_date,
        to_date=to_date,
        timezone=get_settings().timezone,
    )


def _require_limit(limit: int) -> None:
    if type(limit) is not int or not (
        MIN_LIMIT <= limit <= MAX_LIMIT
    ):
        raise ValueError(
            f"limit must be an integer from {MIN_LIMIT} "
            f"to {MAX_LIMIT}."
        )
