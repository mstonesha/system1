"""Morning-versus-afternoon comparison windows.

The unit is a committed WorkSession whose local start time falls
in morning (09:00–11:59) or afternoon (12:00–17:59). Afternoon is
the existing ``early_afternoon`` and ``late_afternoon`` dayparts
combined. Early morning, evening, and overnight are excluded here
and remain available through daypart analytics.

Classification uses ``WorkSession.started_at`` converted to
``APP_TIMEZONE``. A session that starts at 11:55 and ends at
12:20 is a morning observation.

Requested dates are inclusive local calendar dates. ISO weeks
are Monday-start; partial boundary weeks are included and not
expanded. Callers choose windows; this module does not define
historical, recent, or baseline periods.
"""

from datetime import date, timedelta

from sqlalchemy import Date, Integer, cast, extract, func, or_
from sqlalchemy.orm import Session

from app.analytics.dayparts import daypart_sql
from app.analytics.types import (
    COMMITTED_OUTCOMES,
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    MorningAfternoonWindow,
    MorningAfternoonWindowComparison,
    WeeklyMorningAfternoonAnalysis,
    WeeklyMorningAfternoonGroup,
)
from app.config import get_settings
from app.models import WorkSession
from app.time import local_day_bounds_utc


COMPLETED_SESSION_STATE = "completed"
MORNING_DAYPART = "morning"
AFTERNOON_DAYPARTS = (
    "early_afternoon",
    "late_afternoon",
)


def rolling_window_dates(
    to_date: date,
    *,
    weeks: int,
) -> tuple[date, date]:
    """Inclusive local dates for ``weeks`` ISO weeks ending on ``to_date``.

    The window starts on the Monday of the ISO week that is
    ``weeks - 1`` weeks before ``to_date``'s ISO week. ``to_date``
    is not expanded to the following Sunday. Does not read the
    system clock.
    """
    if weeks < 1:
        raise ValueError("weeks must be at least 1.")
    week_start = to_date - timedelta(days=to_date.isoweekday() - 1)
    from_date = week_start - timedelta(weeks=weeks - 1)
    return from_date, to_date


def morning_afternoon_window(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> MorningAfternoonWindow:
    """Morning vs afternoon outcome rates in an explicit local period."""
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    local_started = _local_started_sql(timezone_name)
    daypart = daypart_sql(local_started)
    is_morning = daypart == MORNING_DAYPART
    is_afternoon = daypart.in_(AFTERNOON_DAYPARTS)
    positive = WorkSession.outcome.in_(POSITIVE_OUTCOMES)
    negative = WorkSession.outcome.in_(NEGATIVE_OUTCOMES)
    row = (
        db.query(
            func.count().filter(is_morning).label("m_n"),
            func.count()
            .filter(is_morning, positive)
            .label("m_pos"),
            func.count()
            .filter(is_morning, negative)
            .label("m_neg"),
            func.count().filter(is_afternoon).label("a_n"),
            func.count()
            .filter(is_afternoon, positive)
            .label("a_pos"),
            func.count()
            .filter(is_afternoon, negative)
            .label("a_neg"),
        )
        .filter(
            WorkSession.started_at >= start_utc,
            WorkSession.started_at < end_utc,
            *_committed_session_filters(),
            or_(is_morning, is_afternoon),
        )
        .one()
    )
    return _window_from_counts(
        from_date=from_date,
        to_date=to_date,
        timezone_name=timezone_name,
        morning_n=int(row.m_n),
        morning_pos=int(row.m_pos),
        morning_neg=int(row.m_neg),
        afternoon_n=int(row.a_n),
        afternoon_pos=int(row.a_pos),
        afternoon_neg=int(row.a_neg),
    )


def weekly_morning_afternoon_outcomes(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> WeeklyMorningAfternoonAnalysis:
    """ISO-week morning vs afternoon rates inside the requested period."""
    start_utc, end_utc, timezone_name = _period_bounds(
        from_date,
        to_date,
    )
    local_started = _local_started_sql(timezone_name)
    local_date = cast(local_started, Date)
    week_start = _iso_week_start_sql(local_date)
    daypart = daypart_sql(local_started)
    is_morning = daypart == MORNING_DAYPART
    is_afternoon = daypart.in_(AFTERNOON_DAYPARTS)
    positive = WorkSession.outcome.in_(POSITIVE_OUTCOMES)
    negative = WorkSession.outcome.in_(NEGATIVE_OUTCOMES)
    rows = (
        db.query(
            week_start.label("week_start_date"),
            func.count().filter(is_morning).label("m_n"),
            func.count()
            .filter(is_morning, positive)
            .label("m_pos"),
            func.count()
            .filter(is_morning, negative)
            .label("m_neg"),
            func.count().filter(is_afternoon).label("a_n"),
            func.count()
            .filter(is_afternoon, positive)
            .label("a_pos"),
            func.count()
            .filter(is_afternoon, negative)
            .label("a_neg"),
        )
        .filter(
            WorkSession.started_at >= start_utc,
            WorkSession.started_at < end_utc,
            *_committed_session_filters(),
            or_(is_morning, is_afternoon),
        )
        .group_by(week_start)
        .order_by(week_start)
        .all()
    )
    groups = tuple(_weekly_group(row) for row in rows)
    return WeeklyMorningAfternoonAnalysis(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        groups=groups,
    )


def compare_morning_afternoon_windows(
    db: Session,
    *,
    current_from_date: date,
    current_to_date: date,
    baseline_from_date: date,
    baseline_to_date: date,
) -> MorningAfternoonWindowComparison:
    """Arithmetic comparison of two caller-chosen windows."""
    current = morning_afternoon_window(
        db,
        from_date=current_from_date,
        to_date=current_to_date,
    )
    baseline = morning_afternoon_window(
        db,
        from_date=baseline_from_date,
        to_date=baseline_to_date,
    )
    return MorningAfternoonWindowComparison(
        current=current,
        baseline=baseline,
        morning_rate_change=_delta(
            current.morning_positive_rate,
            baseline.morning_positive_rate,
        ),
        afternoon_rate_change=_delta(
            current.afternoon_positive_rate,
            baseline.afternoon_positive_rate,
        ),
        gap_change=_delta(
            current.positive_rate_gap,
            baseline.positive_rate_gap,
        ),
    )


def _period_bounds(from_date: date, to_date: date) -> tuple:
    if from_date > to_date:
        raise ValueError(
            "from_date must be on or before to_date."
        )
    start_utc, _ = local_day_bounds_utc(from_date)
    _, end_utc = local_day_bounds_utc(to_date)
    return start_utc, end_utc, get_settings().timezone


def _local_started_sql(timezone_name: str):
    return func.timezone(
        timezone_name,
        WorkSession.started_at,
    )


def _iso_week_start_sql(date_expr):
    isodow = cast(extract("isodow", date_expr), Integer)
    return date_expr - (isodow - 1)


def _committed_session_filters():
    return (
        WorkSession.session_state == COMPLETED_SESSION_STATE,
        WorkSession.outcome.in_(COMMITTED_OUTCOMES),
    )


def _rate(positive: int, total: int) -> float | None:
    if total == 0:
        return None
    return positive / total


def _window_from_counts(
    *,
    from_date: date,
    to_date: date,
    timezone_name: str,
    morning_n: int,
    morning_pos: int,
    morning_neg: int,
    afternoon_n: int,
    afternoon_pos: int,
    afternoon_neg: int,
) -> MorningAfternoonWindow:
    morning_rate = _rate(morning_pos, morning_n)
    afternoon_rate = _rate(afternoon_pos, afternoon_n)
    if morning_rate is None or afternoon_rate is None:
        gap = None
    else:
        gap = morning_rate - afternoon_rate
    return MorningAfternoonWindow(
        from_date=from_date,
        to_date=to_date,
        timezone=timezone_name,
        morning_session_count=morning_n,
        morning_positive_count=morning_pos,
        morning_negative_count=morning_neg,
        morning_positive_rate=morning_rate,
        afternoon_session_count=afternoon_n,
        afternoon_positive_count=afternoon_pos,
        afternoon_negative_count=afternoon_neg,
        afternoon_positive_rate=afternoon_rate,
        positive_rate_gap=gap,
    )


def _weekly_group(row) -> WeeklyMorningAfternoonGroup:
    morning_n = int(row.m_n)
    morning_pos = int(row.m_pos)
    morning_neg = int(row.m_neg)
    afternoon_n = int(row.a_n)
    afternoon_pos = int(row.a_pos)
    afternoon_neg = int(row.a_neg)
    morning_rate = _rate(morning_pos, morning_n)
    afternoon_rate = _rate(afternoon_pos, afternoon_n)
    if morning_rate is None or afternoon_rate is None:
        gap = None
    else:
        gap = morning_rate - afternoon_rate
    return WeeklyMorningAfternoonGroup(
        week_start_date=row.week_start_date,
        morning_session_count=morning_n,
        morning_positive_count=morning_pos,
        morning_negative_count=morning_neg,
        morning_positive_rate=morning_rate,
        afternoon_session_count=afternoon_n,
        afternoon_positive_count=afternoon_pos,
        afternoon_negative_count=afternoon_neg,
        afternoon_positive_rate=afternoon_rate,
        positive_rate_gap=gap,
    )


def _delta(current: float | None, baseline: float | None):
    if current is None or baseline is None:
        return None
    return current - baseline
