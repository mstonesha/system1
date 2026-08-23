"""Deterministic WorkSession outcome aggregates.

Aggregation happens in PostgreSQL. Callers pass a SQLAlchemy
session and an inclusive local-calendar period; they cannot
supply arbitrary SQL. Grouping keys are interruption status
or local weekday/daypart derived from ``started_at`` after
converting to ``APP_TIMEZONE``.
"""

from datetime import date

from sqlalchemy import Integer, cast, extract, func
from sqlalchemy.orm import Session

from app.analytics.dayparts import (
    daypart_rank_sql,
    daypart_sql,
)
from app.analytics.types import (
    COMMITTED_OUTCOMES,
    DaypartOutcomeGroup,
    InterruptionOutcomeGroup,
    OutcomeAnalysis,
    WeekdayDaypartOutcomeGroup,
    WeekdayOutcomeGroup,
    iso_weekday_name,
)
from app.config import get_settings
from app.models import WorkSession
from app.time import local_day_bounds_utc


COMPLETED_SESSION_STATE = "completed"


def session_outcomes_by_weekday(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> OutcomeAnalysis[WeekdayOutcomeGroup]:
    """Outcome counts and rates grouped by local weekday.

    Returns one group per weekday that has at least one
    committed session in the period, in ISO weekday order.
    """
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    local_started = _local_started_sql(timezone_name)
    weekday_number = _iso_weekday_sql(local_started)
    rows = _aggregate_outcome_counts(
        db,
        start_utc=start_utc,
        end_utc=end_utc,
        group_columns=(
            weekday_number.label("weekday_number"),
        ),
        group_by=(weekday_number,),
        order_by=(weekday_number,),
    )
    groups = tuple(
        _weekday_group(row) for row in rows
    )
    return _analysis(
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
        groups=groups,
    )


def session_outcomes_by_daypart(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> OutcomeAnalysis[DaypartOutcomeGroup]:
    """Outcome counts and rates grouped by local daypart.

    Returns one group per daypart that has at least one
    committed session in the period, in calendar daypart
    order from overnight through evening.
    """
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    local_started = _local_started_sql(timezone_name)
    daypart = daypart_sql(local_started)
    rows = _aggregate_outcome_counts(
        db,
        start_utc=start_utc,
        end_utc=end_utc,
        group_columns=(daypart.label("daypart"),),
        group_by=(daypart,),
        order_by=(daypart_rank_sql(daypart),),
    )
    groups = tuple(
        _daypart_group(row) for row in rows
    )
    return _analysis(
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
        groups=groups,
    )


def session_outcomes_by_weekday_daypart(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> OutcomeAnalysis[WeekdayDaypartOutcomeGroup]:
    """Outcome counts and rates for weekday × daypart cells.

    Only cells with at least one committed session are
    returned. Order is ISO weekday, then daypart.
    """
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    local_started = _local_started_sql(timezone_name)
    weekday_number = _iso_weekday_sql(local_started)
    daypart = daypart_sql(local_started)
    rows = _aggregate_outcome_counts(
        db,
        start_utc=start_utc,
        end_utc=end_utc,
        group_columns=(
            weekday_number.label("weekday_number"),
            daypart.label("daypart"),
        ),
        group_by=(weekday_number, daypart),
        order_by=(
            weekday_number,
            daypart_rank_sql(daypart),
        ),
    )
    groups = tuple(
        _weekday_daypart_group(row) for row in rows
    )
    return _analysis(
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
        groups=groups,
    )


def session_outcomes_by_interruption(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> OutcomeAnalysis[InterruptionOutcomeGroup]:
    """Outcome counts and rates grouped by interruption.

    Returns one group per interruption status that has at
    least one committed session in the period. Uninterrupted
    (false) precedes interrupted (true).
    """
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    interrupted = WorkSession.interrupted
    rows = _aggregate_outcome_counts(
        db,
        start_utc=start_utc,
        end_utc=end_utc,
        group_columns=(
            interrupted.label("interrupted"),
        ),
        group_by=(interrupted,),
        order_by=(interrupted,),
    )
    groups = tuple(
        _interruption_group(row) for row in rows
    )
    return _analysis(
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
        groups=groups,
    )


def _period_bounds(
    from_date: date,
    to_date: date,
) -> tuple:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )
    start_utc, _ = local_day_bounds_utc(from_date)
    _, end_utc = local_day_bounds_utc(to_date)
    return start_utc, end_utc, get_settings().timezone


def _local_started_sql(timezone_name: str):
    """Convert stored UTC timestamptz to local wall time.

    PostgreSQL ``timezone(zone, timestamptz)`` returns a
    timestamp without time zone in that zone, so EXTRACT
    and time-of-day classification use local clock values
    across GMT/BST transitions.
    """
    return func.timezone(
        timezone_name,
        WorkSession.started_at,
    )


def _iso_weekday_sql(local_started):
    return cast(
        extract("isodow", local_started),
        Integer,
    )


def _count_outcome(outcome: str):
    return func.count().filter(
        WorkSession.outcome == outcome
    )


def _aggregate_outcome_counts(
    db: Session,
    *,
    start_utc,
    end_utc,
    group_columns: tuple,
    group_by: tuple,
    order_by: tuple,
):
    return (
        db.query(
            *group_columns,
            func.count().label("session_count"),
            _count_outcome("progress").label(
                "progress_count"
            ),
            _count_outcome("complete").label(
                "complete_count"
            ),
            _count_outcome("stuck").label("stuck_count"),
            _count_outcome("paused").label("paused_count"),
            _count_outcome("abandoned").label(
                "abandoned_count"
            ),
        )
        .filter(
            WorkSession.started_at >= start_utc,
            WorkSession.started_at < end_utc,
            WorkSession.session_state
            == COMPLETED_SESSION_STATE,
            WorkSession.outcome.in_(COMMITTED_OUTCOMES),
        )
        .group_by(*group_by)
        .order_by(*order_by)
        .all()
    )


def _counts_from_row(row) -> dict:
    session_count = int(row.session_count)
    progress_count = int(row.progress_count)
    complete_count = int(row.complete_count)
    stuck_count = int(row.stuck_count)
    paused_count = int(row.paused_count)
    abandoned_count = int(row.abandoned_count)
    positive_count = progress_count + complete_count
    negative_count = (
        stuck_count + paused_count + abandoned_count
    )
    if session_count == 0:
        positive_rate = 0.0
        negative_rate = 0.0
    else:
        positive_rate = positive_count / session_count
        negative_rate = negative_count / session_count
    return {
        "session_count": session_count,
        "progress_count": progress_count,
        "complete_count": complete_count,
        "stuck_count": stuck_count,
        "paused_count": paused_count,
        "abandoned_count": abandoned_count,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "positive_rate": positive_rate,
        "negative_rate": negative_rate,
    }


def _weekday_group(row) -> WeekdayOutcomeGroup:
    weekday_number = int(row.weekday_number)
    return WeekdayOutcomeGroup(
        weekday=iso_weekday_name(weekday_number),
        weekday_number=weekday_number,
        **_counts_from_row(row),
    )


def _daypart_group(row) -> DaypartOutcomeGroup:
    return DaypartOutcomeGroup(
        daypart=row.daypart,
        **_counts_from_row(row),
    )


def _weekday_daypart_group(
    row,
) -> WeekdayDaypartOutcomeGroup:
    weekday_number = int(row.weekday_number)
    return WeekdayDaypartOutcomeGroup(
        weekday=iso_weekday_name(weekday_number),
        weekday_number=weekday_number,
        daypart=row.daypart,
        **_counts_from_row(row),
    )


def _interruption_group(row) -> InterruptionOutcomeGroup:
    return InterruptionOutcomeGroup(
        interrupted=bool(row.interrupted),
        **_counts_from_row(row),
    )


def _analysis(
    *,
    from_date: date,
    to_date: date,
    timezone_name: str,
    groups: tuple,
) -> OutcomeAnalysis:
    return OutcomeAnalysis(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        total_sessions=sum(
            group.session_count for group in groups
        ),
        groups=groups,
    )
