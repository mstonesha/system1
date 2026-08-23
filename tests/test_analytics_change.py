from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.analytics.change import (
    compare_morning_afternoon_windows,
    morning_afternoon_window,
    rolling_window_dates,
    weekly_morning_afternoon_outcomes,
)
from app.models import WorkSession
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
FORBIDDEN = {
    "behaviour_changed",
    "change_detected",
    "historical_pattern",
    "current_pattern",
    "improved",
    "declined",
    "stale",
    "trend",
    "confidence",
    "significance",
    "recommendation",
    "regime",
    "phase",
}


def _local(year, month, day, hour, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=LONDON,
    )


def _add_completed(
    db,
    make_task,
    make_daily_task,
    local_started,
    outcome="progress",
):
    task = make_task()
    daily = make_daily_task(
        task,
        target_date=local_started.date(),
    )
    started_at = local_started.astimezone(UTC)
    work = WorkSession(
        daily_task_id=daily.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state="completed",
        outcome=outcome,
    )
    db.add(work)
    db.commit()
    return work


def test_rolling_window_dates_uses_iso_weeks_and_keeps_anchor():
    from_date, to_date = rolling_window_dates(
        date(2027, 5, 14),
        weeks=8,
    )
    assert from_date == date(2027, 3, 22)
    assert to_date == date(2027, 5, 14)
    with pytest.raises(ValueError, match="weeks"):
        rolling_window_dates(date(2027, 5, 14), weeks=0)


def test_empty_period(db):
    result = morning_afternoon_window(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    weeks = weekly_morning_afternoon_outcomes(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    assert result.morning_session_count == 0
    assert result.afternoon_session_count == 0
    assert result.morning_positive_rate is None
    assert result.afternoon_positive_rate is None
    assert result.positive_rate_gap is None
    assert weeks.groups == ()
    assert FORBIDDEN.isdisjoint(result.__dataclass_fields__)
    assert FORBIDDEN.isdisjoint(weeks.__dataclass_fields__)


def test_inverted_period_is_rejected(db):
    with pytest.raises(ValueError, match="from_date"):
        morning_afternoon_window(
            db,
            from_date=date(2026, 1, 10),
            to_date=date(2026, 1, 9),
        )


def test_morning_only_leaves_gap_undefined(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 10),
        "progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 11),
        "stuck",
    )
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 2
    assert result.morning_positive_count == 1
    assert result.morning_negative_count == 1
    assert result.morning_positive_rate == 0.5
    assert result.afternoon_session_count == 0
    assert result.afternoon_positive_rate is None
    assert result.positive_rate_gap is None


def test_afternoon_only_leaves_gap_undefined(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 13),
        "complete",
    )
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.afternoon_session_count == 1
    assert result.afternoon_positive_rate == 1.0
    assert result.morning_session_count == 0
    assert result.morning_positive_rate is None
    assert result.positive_rate_gap is None


def test_gap_and_counts_reconcile(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 10), "progress"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 11), "stuck"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 13), "paused"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 16), "complete"
    )
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 2
    assert result.afternoon_session_count == 2
    assert (
        result.morning_positive_count
        + result.morning_negative_count
        == result.morning_session_count
    )
    assert (
        result.afternoon_positive_count
        + result.afternoon_negative_count
        == result.afternoon_session_count
    )
    assert result.morning_positive_rate == 0.5
    assert result.afternoon_positive_rate == 0.5
    assert result.positive_rate_gap == 0.0


def test_sessions_outside_focus_hours_are_excluded(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 8, 59)
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 18)
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 15, 2)
    )
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 0
    assert result.afternoon_session_count == 0


def test_boundary_hours_use_start_time(
    db,
    make_task,
    make_daily_task,
):
    morning_edge = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 11, 59),
        "progress",
    )
    afternoon_start = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 12),
        "stuck",
    )
    afternoon_edge = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 17, 59),
        "complete",
    )
    morning_edge.ended_at = morning_edge.started_at + timedelta(
        minutes=40
    )
    db.commit()
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 1
    assert result.afternoon_session_count == 2
    assert result.morning_positive_count == 1
    assert result.afternoon_positive_count == 1
    assert afternoon_start.started_at < afternoon_edge.started_at


def test_gmt_start_time_classifies_local_morning(
    db,
    make_task,
    make_daily_task,
):
    utc_eleven = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
    _add_completed(
        db,
        make_task,
        make_daily_task,
        utc_eleven.astimezone(LONDON),
        "progress",
    )
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 1
    assert result.afternoon_session_count == 0


def test_running_session_is_excluded(
    db,
    make_task,
    make_daily_task,
):
    task = make_task()
    daily = make_daily_task(task, target_date=date(2026, 1, 15))
    started = _local(2026, 1, 15, 10).astimezone(UTC)
    db.add(
        WorkSession(
            daily_task_id=daily.id,
            started_at=started,
            ended_at=None,
            planned_duration_seconds=1500,
            session_state="running",
            outcome=None,
        )
    )
    db.commit()
    result = morning_afternoon_window(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.morning_session_count == 0


def test_weekly_partial_week_and_cross_year(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 1, 10),
        "progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 1, 13),
        "stuck",
    )
    weeks = weekly_morning_afternoon_outcomes(
        db,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 1),
    )
    assert len(weeks.groups) == 1
    group = weeks.groups[0]
    assert group.week_start_date == date(2025, 12, 29)
    assert group.morning_session_count == 1
    assert group.afternoon_session_count == 1
    assert group.positive_rate_gap == 1.0


def test_compare_windows_returns_arithmetic_deltas(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 12, 10), "progress"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 12, 13), "stuck"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 19, 10), "stuck"
    )
    _add_completed(
        db, make_task, make_daily_task, _local(2026, 1, 19, 13), "progress"
    )
    compared = compare_morning_afternoon_windows(
        db,
        current_from_date=date(2026, 1, 19),
        current_to_date=date(2026, 1, 19),
        baseline_from_date=date(2026, 1, 12),
        baseline_to_date=date(2026, 1, 12),
    )
    assert compared.baseline.positive_rate_gap == 1.0
    assert compared.current.positive_rate_gap == -1.0
    assert compared.gap_change == -2.0
    assert compared.morning_rate_change == -1.0
    assert compared.afternoon_rate_change == 1.0
    assert FORBIDDEN.isdisjoint(compared.__dataclass_fields__)
