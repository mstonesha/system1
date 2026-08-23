from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.analytics.planning import (
    daily_planning_summary,
    task_effort_estimation,
    weekly_workload,
)
from app.models import DailyTask, WorkSession
from app.services.tasks import complete_task
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
FORBIDDEN = {
    "overplanned",
    "underplanned",
    "inefficient",
    "workload_too_high",
    "threshold_exceeded",
    "risk",
    "confidence",
    "significance",
    "recommendation",
    "heavy_week",
    "workload_risk",
    "overplanning_problem",
}


def _local(year, month, day, hour=10, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=LONDON,
    )


def _add_daily(
    db,
    task,
    target_date,
    *,
    planned_sessions=1,
    state="planned",
):
    daily = DailyTask(
        task_id=task.id,
        date=target_date,
        planned_sessions=planned_sessions,
        state=state,
        sort_order=0,
    )
    db.add(daily)
    db.commit()
    db.refresh(daily)
    return daily


def _session_on(
    db,
    daily,
    local_started,
    outcome="progress",
    session_state="completed",
):
    started_at = local_started.astimezone(UTC)
    work = WorkSession(
        daily_task_id=daily.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state=session_state,
        outcome=outcome,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def test_planning_empty_period(db):
    summary = daily_planning_summary(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    effort = task_effort_estimation(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    weeks = weekly_workload(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    assert summary.daily_tasks_with_explicit_plan == 0
    assert summary.total_planned_sessions == 0
    assert summary.execution_ratio is None
    assert effort.completed_tasks_with_estimate == 0
    assert effort.mean_actual_to_estimated_ratio is None
    assert weeks.groups == ()
    assert FORBIDDEN.isdisjoint(summary.__dataclass_fields__)
    assert FORBIDDEN.isdisjoint(effort.__dataclass_fields__)
    assert FORBIDDEN.isdisjoint(weeks.__dataclass_fields__)


def test_inverted_period_is_rejected(db):
    with pytest.raises(ValueError, match="from_date"):
        daily_planning_summary(
            db,
            from_date=date(2026, 1, 10),
            to_date=date(2026, 1, 9),
        )


def test_null_planned_sessions_excluded_from_comparison(
    db,
    make_task,
):
    planned_task = make_task("Planned")
    unplanned_task = make_task("Unplanned")
    day = date(2026, 1, 15)
    planned = _add_daily(
        db,
        planned_task,
        day,
        planned_sessions=3,
    )
    unplanned = _add_daily(
        db,
        unplanned_task,
        day,
        planned_sessions=None,
    )
    _session_on(db, planned, _local(2026, 1, 15), "progress")
    _session_on(
        db,
        unplanned,
        _local(2026, 1, 15, 11),
        "progress",
    )

    summary = daily_planning_summary(
        db,
        from_date=day,
        to_date=day,
    )
    assert summary.daily_tasks_with_explicit_plan == 1
    assert summary.daily_tasks_without_explicit_plan == 1
    assert summary.total_planned_sessions == 3
    assert summary.total_actual_sessions == 1
    assert summary.total_unused_planned_sessions == 2


def test_running_session_is_not_actual_work(
    db,
    make_task,
):
    task = make_task("Running")
    daily = _add_daily(
        db,
        task,
        date(2026, 1, 15),
        planned_sessions=2,
    )
    _session_on(
        db,
        daily,
        _local(2026, 1, 15),
        "progress",
    )
    _session_on(
        db,
        daily,
        _local(2026, 1, 15, 11),
        outcome=None,
        session_state="running",
    )
    summary = daily_planning_summary(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert summary.total_actual_sessions == 1
    assert summary.total_unused_planned_sessions == 1


def test_unused_capacity_classes(
    db,
    make_task,
):
    early = make_task("Early")
    unfinished = make_task("Unfinished")
    abandoned = make_task("Abandoned")
    extra = make_task("Extra")
    day = date(2026, 1, 15)
    early_daily = _add_daily(
        db, early, day, planned_sessions=4, state="completed"
    )
    unfinished_daily = _add_daily(
        db, unfinished, day, planned_sessions=4
    )
    abandoned_daily = _add_daily(
        db,
        abandoned,
        day,
        planned_sessions=4,
        state="abandoned",
    )
    extra_daily = _add_daily(
        db, extra, day, planned_sessions=1
    )
    for daily in (early_daily, unfinished_daily, abandoned_daily):
        _session_on(db, daily, _local(2026, 1, 15), "progress")
        _session_on(
            db,
            daily,
            _local(2026, 1, 15, 11),
            "stuck",
        )
    _session_on(db, extra_daily, _local(2026, 1, 15, 12), "progress")
    _session_on(
        db,
        extra_daily,
        _local(2026, 1, 15, 13),
        "progress",
    )

    summary = daily_planning_summary(db, from_date=day, to_date=day)
    assert summary.unused_due_to_early_completion == 2
    assert summary.unused_while_unfinished == 2
    assert summary.unused_on_abandonment == 2
    assert summary.total_unused_planned_sessions == 6
    assert summary.daily_tasks_actual_below_plan == 3
    assert summary.daily_tasks_actual_equal_plan == 0
    assert summary.daily_tasks_actual_above_plan == 1
    assert summary.execution_ratio == 8 / 13


def test_removed_daily_task_excluded_from_capacity(
    db,
    make_task,
):
    task = make_task("Removed")
    _add_daily(
        db,
        task,
        date(2026, 1, 15),
        planned_sessions=4,
        state="removed",
    )
    summary = daily_planning_summary(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert summary.daily_tasks_with_explicit_plan == 0
    assert summary.total_planned_sessions == 0


def test_capacity_date_filter_uses_daily_task_date(
    db,
    make_task,
):
    inside = make_task("Inside")
    outside = make_task("Outside")
    _add_daily(
        db, inside, date(2026, 1, 15), planned_sessions=2
    )
    _add_daily(
        db, outside, date(2026, 1, 16), planned_sessions=5
    )
    summary = daily_planning_summary(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert summary.total_planned_sessions == 2


def test_effort_completed_lifetime_across_dailies(
    db,
    make_task,
):
    task = make_task("Long", estimated_sessions=3)
    first = _add_daily(db, task, date(2026, 1, 5), planned_sessions=2)
    last = _add_daily(
        db,
        task,
        date(2026, 1, 20),
        planned_sessions=2,
        state="completed",
    )
    _session_on(db, first, _local(2026, 1, 5), "progress")
    _session_on(db, first, _local(2026, 1, 5, 11), "progress")
    _session_on(db, last, _local(2026, 1, 20), "complete")
    task.status = "completed"
    task.completed_at = _local(2026, 1, 20, 11).astimezone(UTC)
    db.commit()

    inside = task_effort_estimation(
        db,
        from_date=date(2026, 1, 18),
        to_date=date(2026, 1, 21),
    )
    before = task_effort_estimation(
        db,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 10),
    )
    assert inside.completed_tasks_with_estimate == 1
    assert inside.mean_estimated_sessions == 3
    assert inside.mean_actual_sessions == 3
    assert inside.near_estimate_count == 1
    assert inside.mean_actual_to_estimated_ratio == 1.0
    assert inside.median_actual_to_estimated_ratio == 1.0
    assert before.completed_tasks_with_estimate == 0


def test_effort_excludes_null_estimate_active_and_cancelled(
    db,
    make_task,
):
    no_estimate = make_task("No estimate")
    active = make_task("Active", estimated_sessions=2)
    cancelled = make_task("Cancelled", estimated_sessions=2)
    cancelled.status = "cancelled"
    db.commit()
    daily = _add_daily(
        db,
        no_estimate,
        date(2026, 1, 15),
        planned_sessions=1,
        state="completed",
    )
    _session_on(db, daily, _local(2026, 1, 15), "complete")
    complete_task(db, no_estimate)
    _add_daily(db, active, date(2026, 1, 15), planned_sessions=2)
    abandoned = _add_daily(
        db,
        cancelled,
        date(2026, 1, 15),
        planned_sessions=2,
        state="abandoned",
    )
    _session_on(db, abandoned, _local(2026, 1, 15), "abandoned")

    result = task_effort_estimation(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.completed_tasks_with_estimate == 0


def test_effort_below_near_above_and_median(db, make_task):
    specs = (
        ("Below", 4, 2, "below"),
        ("Near", 3, 3, "near"),
        ("Above", 2, 4, "above"),
        ("Also above", 2, 3, "above"),
    )
    day = date(2026, 1, 15)
    for title, estimated, actual, _band in specs:
        task = make_task(title, estimated_sessions=estimated)
        daily = _add_daily(
            db,
            task,
            day,
            planned_sessions=actual,
            state="completed",
        )
        for index in range(actual):
            _session_on(
                db,
                daily,
                _local(2026, 1, 15, 9 + index),
                "complete" if index == actual - 1 else "progress",
            )
        task.status = "completed"
        task.completed_at = _local(2026, 1, 15, 18).astimezone(
            UTC
        )
        db.commit()

    result = task_effort_estimation(db, from_date=day, to_date=day)
    assert result.below_estimate_count == 1
    assert result.near_estimate_count == 1
    assert result.above_estimate_count == 2
    ratios = (2 / 4, 3 / 3, 4 / 2, 3 / 2)
    assert result.mean_actual_to_estimated_ratio == (
        sum(ratios) / 4
    )
    ordered = sorted(ratios)
    assert result.median_actual_to_estimated_ratio == (
        (ordered[1] + ordered[2]) / 2
    )


def test_weekly_iso_week_and_cross_year(db, make_task):
    task = make_task("Year boundary")
    daily = _add_daily(
        db,
        task,
        date(2026, 1, 1),
        planned_sessions=2,
    )
    _session_on(db, daily, _local(2026, 1, 1), "progress")
    weeks = weekly_workload(
        db,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 1),
    )
    assert len(weeks.groups) == 1
    group = weeks.groups[0]
    assert group.week_start_date == date(2025, 12, 29)
    assert group.daily_task_count == 1
    assert group.planned_sessions == 2
    assert group.actual_sessions == 1
    assert group.positive_sessions == 1
    assert group.negative_sessions == 0
    assert group.positive_rate == 1.0


def test_weekly_partial_boundary_week(db, make_task):
    monday = make_task("Monday")
    wednesday = make_task("Wednesday")
    _add_daily(
        db, monday, date(2026, 1, 12), planned_sessions=3
    )
    wed = _add_daily(
        db, wednesday, date(2026, 1, 14), planned_sessions=2
    )
    _session_on(db, wed, _local(2026, 1, 14), "stuck")
    weeks = weekly_workload(
        db,
        from_date=date(2026, 1, 14),
        to_date=date(2026, 1, 14),
    )
    assert len(weeks.groups) == 1
    group = weeks.groups[0]
    assert group.week_start_date == date(2026, 1, 12)
    assert group.daily_task_count == 1
    assert group.planned_sessions == 2
    assert group.actual_sessions == 1
    assert group.negative_sessions == 1
    assert group.positive_rate == 0.0
    assert group.negative_rate == 1.0


def test_weekly_bst_session_uses_local_week(db, make_task):
    task = make_task("BST")
    daily = _add_daily(
        db,
        task,
        date(2026, 7, 15),
        planned_sessions=1,
    )
    utc_near_midnight = datetime(
        2026, 7, 14, 23, 30, tzinfo=UTC
    )
    _session_on(
        db,
        daily,
        utc_near_midnight.astimezone(LONDON),
        "progress",
    )
    weeks = weekly_workload(
        db,
        from_date=date(2026, 7, 15),
        to_date=date(2026, 7, 15),
    )
    assert weeks.groups[0].week_start_date == date(2026, 7, 13)
    assert weeks.groups[0].actual_sessions == 1
    previous = weekly_workload(
        db,
        from_date=date(2026, 7, 14),
        to_date=date(2026, 7, 14),
    )
    assert previous.groups == ()


def test_weekly_gmt_session_stays_on_utc_calendar_date(
    db,
    make_task,
):
    task = make_task("GMT")
    daily = _add_daily(
        db,
        task,
        date(2026, 1, 14),
        planned_sessions=1,
    )
    utc_near_midnight = datetime(
        2026, 1, 14, 23, 30, tzinfo=UTC
    )
    _session_on(
        db,
        daily,
        utc_near_midnight.astimezone(LONDON),
        "progress",
    )
    on_day = weekly_workload(
        db,
        from_date=date(2026, 1, 14),
        to_date=date(2026, 1, 14),
    )
    next_day = weekly_workload(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert on_day.groups[0].week_start_date == date(2026, 1, 12)
    assert on_day.groups[0].actual_sessions == 1
    assert next_day.groups == ()


def test_weekly_positive_plus_negative_equals_actual(
    db,
    make_task,
):
    task = make_task("Mixed week")
    daily = _add_daily(
        db,
        task,
        date(2026, 1, 14),
        planned_sessions=4,
    )
    _session_on(db, daily, _local(2026, 1, 14, 9), "progress")
    _session_on(db, daily, _local(2026, 1, 14, 10), "stuck")
    _session_on(db, daily, _local(2026, 1, 14, 11), "paused")
    _session_on(
        db, daily, _local(2026, 1, 14, 12), "abandoned"
    )
    weeks = weekly_workload(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
    )
    group = weeks.groups[0]
    assert group.positive_sessions + group.negative_sessions == (
        group.actual_sessions
    )
    assert group.positive_sessions == 1
    assert group.negative_sessions == 3
    assert group.daily_task_count == 1
    assert group.planned_sessions == 4


def test_only_null_plans_leave_execution_ratio_undefined(
    db,
    make_task,
):
    task = make_task("Unplanned only")
    daily = _add_daily(
        db,
        task,
        date(2026, 1, 15),
        planned_sessions=None,
    )
    _session_on(db, daily, _local(2026, 1, 15), "progress")
    summary = daily_planning_summary(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert summary.daily_tasks_with_explicit_plan == 0
    assert summary.total_planned_sessions == 0
    assert summary.total_actual_sessions == 0
    assert summary.execution_ratio is None
