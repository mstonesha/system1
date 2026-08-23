from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.analytics.task_age import (
    classify_execution_age_days,
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.models import DailyTask, Task, WorkSession
from app.services.tasks import cancel_task, complete_task, reopen_task
from app.services.today import add_task_to_day, remove_task_from_day
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
FORBIDDEN_FIELDS = {
    "risk",
    "threshold",
    "warning",
    "significance",
    "confidence",
    "recommendation",
    "likely_to_fail",
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


def _set_created_at(db, task, local_dt):
    task.created_at = local_dt.astimezone(UTC)
    db.commit()
    db.refresh(task)


def _add_daily(db, task, target_date, state="planned"):
    daily = DailyTask(
        task_id=task.id,
        date=target_date,
        planned_sessions=1,
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
    outcome,
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


def _complete_task_on(db, task, daily, local_started):
    _session_on(db, daily, local_started, "complete")
    daily.state = "completed"
    task.status = "completed"
    task.completed_at = (
        local_started + timedelta(minutes=25)
    ).astimezone(UTC)
    db.commit()
    db.refresh(task)
    db.refresh(daily)


def _abandon_task_on(db, task, daily, local_started):
    _session_on(db, daily, local_started, "abandoned")
    daily.state = "abandoned"
    task.status = "cancelled"
    task.completed_at = None
    db.commit()
    db.refresh(task)
    db.refresh(daily)


def _analyze(db, from_date, to_date):
    return task_abandonment_by_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "0-2"),
        (2, "0-2"),
        (3, "3-7"),
        (7, "3-7"),
        (8, "8-14"),
        (14, "8-14"),
        (15, "15-30"),
        (30, "15-30"),
        (31, "31+"),
        (90, "31+"),
    ],
)
def test_classify_execution_age_days(days, expected):
    assert classify_execution_age_days(days) == expected


def test_empty_period_returns_empty_task_age(db):
    result = _analyze(db, date(2020, 1, 6), date(2020, 1, 10))
    assert result.total_terminal_tasks == 0
    assert result.groups == ()
    assert result.timezone == "Europe/London"
    assert FORBIDDEN_FIELDS.isdisjoint(
        result.__dataclass_fields__
    )


def test_inverted_period_is_rejected(db):
    with pytest.raises(ValueError, match="from_date"):
        _analyze(db, date(2026, 1, 10), date(2026, 1, 9))


def test_active_tasks_and_session_outcomes_are_not_terminal(
    db,
    make_task,
):
    active = make_task("Still going")
    first = date(2026, 1, 12)
    daily = _add_daily(db, active, first)
    _session_on(
        db,
        daily,
        _local(2026, 1, 12),
        "progress",
    )
    stuck = make_task("Stuck only")
    stuck_daily = _add_daily(db, stuck, first)
    _session_on(
        db,
        stuck_daily,
        _local(2026, 1, 12, 11),
        "stuck",
    )

    result = _analyze(db, first, first)
    assert result.total_terminal_tasks == 0


def test_completed_and_abandoned_counts_reconcile(
    db,
    make_task,
):
    completed = make_task("Done")
    abandoned = make_task("Dropped")
    day = date(2026, 1, 15)
    complete_daily = _add_daily(db, completed, day)
    abandon_daily = _add_daily(db, abandoned, day)
    _complete_task_on(
        db,
        completed,
        complete_daily,
        _local(2026, 1, 15),
    )
    _abandon_task_on(
        db,
        abandoned,
        abandon_daily,
        _local(2026, 1, 15, 11),
    )

    result = _analyze(db, day, day)
    assert result.total_terminal_tasks == 2
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.age_bucket == "0-2"
    assert group.completed_count == 1
    assert group.abandoned_count == 1
    assert (
        group.completed_count + group.abandoned_count
        == group.terminal_task_count
    )
    assert abs(
        group.abandonment_rate + group.completion_rate - 1.0
    ) < 1e-12
    assert FORBIDDEN_FIELDS.isdisjoint(
        group.__dataclass_fields__
    )


def test_terminal_only_in_requested_local_period(
    db,
    make_task,
):
    early = make_task("Early")
    inside = make_task("Inside")
    late = make_task("Late")
    early_daily = _add_daily(db, early, date(2026, 1, 12))
    inside_first = _add_daily(db, inside, date(2026, 1, 5))
    inside_last = _add_daily(db, inside, date(2026, 1, 15))
    late_daily = _add_daily(db, late, date(2026, 1, 20))
    _complete_task_on(
        db,
        early,
        early_daily,
        _local(2026, 1, 12),
    )
    _complete_task_on(
        db,
        inside,
        inside_last,
        _local(2026, 1, 15),
    )
    _complete_task_on(
        db,
        late,
        late_daily,
        _local(2026, 1, 20),
    )

    result = _analyze(db, date(2026, 1, 14), date(2026, 1, 16))
    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 14),
        to_date=date(2026, 1, 16),
    )
    assert result.total_terminal_tasks == 1
    assert details[0].task_id == inside.id
    assert details[0].first_today_date == date(2026, 1, 5)
    assert details[0].terminal_date == date(2026, 1, 15)
    assert details[0].execution_age_days == 10


def test_no_today_history_is_excluded(db, make_task):
    task = make_task("Never planned")
    complete_task(db, task)

    result = _analyze(
        db,
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    assert result.total_terminal_tasks == 0


def test_list_cancel_without_datable_terminal_is_excluded(
    db,
    make_task,
):
    task = make_task("List cancel")
    add_task_to_day(
        db,
        task,
        date(2026, 1, 15),
        planned_sessions=1,
    )
    cancel_task(db, task)

    result = _analyze(
        db,
        date(2026, 1, 15),
        date(2026, 1, 15),
    )
    assert result.total_terminal_tasks == 0


def test_list_complete_uses_completed_at_local_date(
    db,
    make_task,
):
    task = make_task("List complete")
    add_task_to_day(
        db,
        task,
        date(2026, 7, 15),
        planned_sessions=1,
    )
    complete_task(db, task)
    task.completed_at = _local(2026, 7, 15, 0, 30).astimezone(
        UTC
    )
    db.commit()

    assert task.completed_at.date() == date(2026, 7, 14)
    result = _analyze(db, date(2026, 7, 15), date(2026, 7, 15))
    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 7, 15),
        to_date=date(2026, 7, 15),
    )
    assert result.total_terminal_tasks == 1
    assert details[0].terminal_date == date(2026, 7, 15)
    assert details[0].terminal_outcome == "completed"


def test_removed_then_readded_starts_at_later_today(
    db,
    make_task,
):
    task = make_task("Removed then back")
    first = add_task_to_day(
        db,
        task,
        date(2026, 1, 12),
        planned_sessions=1,
    )
    remove_task_from_day(db, first)
    later = add_task_to_day(
        db,
        task,
        date(2026, 1, 16),
        planned_sessions=1,
    )
    _complete_task_on(
        db,
        task,
        later,
        _local(2026, 1, 16),
    )

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 16),
        to_date=date(2026, 1, 16),
    )
    assert details[0].first_today_date == date(2026, 1, 16)
    assert details[0].execution_age_days == 0


def test_created_at_is_not_execution_age(db, make_task):
    task = make_task("Long backlog")
    _set_created_at(db, task, _local(2026, 5, 1))
    first = _add_daily(db, task, date(2026, 5, 20))
    last = _add_daily(db, task, date(2026, 5, 27))
    _complete_task_on(
        db,
        task,
        last,
        _local(2026, 5, 27),
    )

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 5, 27),
        to_date=date(2026, 5, 27),
    )
    assert details[0].first_today_date == date(2026, 5, 20)
    assert details[0].execution_age_days == 7
    created_age = (
        details[0].terminal_date
        - task.created_at.astimezone(LONDON).date()
    ).days
    assert created_age == 26


def test_calendar_age_includes_weekends(db, make_task):
    task = make_task("Weekend span")
    first = _add_daily(db, task, date(2026, 1, 16))
    last = _add_daily(db, task, date(2026, 1, 19))
    _abandon_task_on(
        db,
        task,
        last,
        _local(2026, 1, 19),
    )

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 19),
        to_date=date(2026, 1, 19),
    )
    assert details[0].execution_age_days == 3
    assert details[0].terminal_outcome == "abandoned"


def test_intermittent_today_uses_first_non_removed_date(
    db,
    make_task,
):
    task = make_task("Gaps")
    _add_daily(db, task, date(2026, 1, 12))
    _add_daily(db, task, date(2026, 1, 15))
    last = _add_daily(db, task, date(2026, 1, 21))
    _complete_task_on(
        db,
        task,
        last,
        _local(2026, 1, 21),
    )

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 21),
        to_date=date(2026, 1, 21),
    )
    assert details[0].first_today_date == date(2026, 1, 12)
    assert details[0].execution_age_days == 9


def test_parent_without_today_history_is_excluded(
    db,
    make_task,
):
    parent = make_task("Parent")
    child = make_task("Child", parent=parent)
    daily = _add_daily(db, child, date(2026, 1, 15))
    _complete_task_on(
        db,
        child,
        daily,
        _local(2026, 1, 15),
    )
    complete_task(db, parent)

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert [row.task_id for row in details] == [child.id]


def test_reopened_active_task_is_excluded(db, make_task):
    task = make_task("Reopened")
    daily = _add_daily(db, task, date(2026, 1, 15))
    _complete_task_on(
        db,
        task,
        daily,
        _local(2026, 1, 15),
    )
    reopen_task(db, task)

    result = _analyze(db, date(2026, 1, 15), date(2026, 1, 15))
    assert result.total_terminal_tasks == 0


def test_reopened_then_recompleted_uses_original_first_today(
    db,
    make_task,
):
    task = make_task("Second life")
    first = _add_daily(db, task, date(2026, 1, 5))
    _complete_task_on(
        db,
        task,
        first,
        _local(2026, 1, 5),
    )
    reopen_task(db, task)
    later = _add_daily(db, task, date(2026, 1, 20))
    _complete_task_on(
        db,
        task,
        later,
        _local(2026, 1, 20),
    )

    details = terminal_tasks_with_execution_age(
        db,
        from_date=date(2026, 1, 20),
        to_date=date(2026, 1, 20),
    )
    assert details[0].first_today_date == date(2026, 1, 5)
    assert details[0].terminal_date == date(2026, 1, 20)
    assert details[0].execution_age_days == 15


def test_age_buckets_are_stable_and_observed_only(
    db,
    make_task,
):
    young = make_task("Young")
    old = make_task("Old")
    young_daily = _add_daily(db, young, date(2026, 1, 15))
    _complete_task_on(
        db,
        young,
        young_daily,
        _local(2026, 1, 15),
    )
    old_first = _add_daily(db, old, date(2025, 12, 1))
    old_last = _add_daily(db, old, date(2026, 1, 15))
    _abandon_task_on(
        db,
        old,
        old_last,
        _local(2026, 1, 15),
    )

    result = _analyze(db, date(2026, 1, 15), date(2026, 1, 15))
    names = [group.age_bucket for group in result.groups]
    assert names == ["0-2", "31+"]
    assert result.groups[0].min_days == 0
    assert result.groups[0].max_days == 2
    assert result.groups[1].min_days == 31
    assert result.groups[1].max_days is None
