from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.analytics.drilldown import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    stuck_task_drilldown,
)
from app.models import WorkSession
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
FORBIDDEN = {
    "category",
    "department",
    "dependency_type",
    "waiting_on",
    "hr_related",
    "cluster",
    "inferred_topic",
    "recommendation",
    "confidence",
    "significance",
    "description",
    "note",
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


def _session(
    db,
    daily,
    local_started,
    outcome="stuck",
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
    return work


def test_empty_period(db):
    result = stuck_task_drilldown(
        db,
        from_date=date(2020, 1, 6),
        to_date=date(2020, 1, 10),
    )
    assert result.total_stuck_sessions == 0
    assert result.total_distinct_stuck_tasks == 0
    assert result.returned_task_count == 0
    assert result.limit == DEFAULT_LIMIT
    assert result.tasks == ()
    assert FORBIDDEN.isdisjoint(result.__dataclass_fields__)


def test_limit_validation(db):
    with pytest.raises(ValueError, match="limit"):
        stuck_task_drilldown(
            db,
            from_date=date(2026, 1, 15),
            to_date=date(2026, 1, 15),
            limit=0,
        )
    with pytest.raises(ValueError, match="limit"):
        stuck_task_drilldown(
            db,
            from_date=date(2026, 1, 15),
            to_date=date(2026, 1, 15),
            limit=-1,
        )
    with pytest.raises(ValueError, match="limit"):
        stuck_task_drilldown(
            db,
            from_date=date(2026, 1, 15),
            to_date=date(2026, 1, 15),
            limit=MAX_LIMIT + 1,
        )
    with pytest.raises(ValueError, match="limit"):
        stuck_task_drilldown(
            db,
            from_date=date(2026, 1, 15),
            to_date=date(2026, 1, 15),
            limit=None,
        )


def test_only_completed_stuck_sessions_count(
    db,
    make_task,
    make_daily_task,
):
    kept = make_task("[HR] Await contract amendment")
    other = make_task("Fix timer DST boundary")
    kept_daily = make_daily_task(kept, target_date=date(2026, 1, 15))
    other_daily = make_daily_task(
        other,
        target_date=date(2026, 1, 15),
    )
    _session(db, kept_daily, _local(2026, 1, 15, 10), "stuck")
    _session(db, kept_daily, _local(2026, 1, 15, 11), "stuck")
    _session(db, other_daily, _local(2026, 1, 15, 12), "progress")
    _session(db, other_daily, _local(2026, 1, 15, 13), "paused")
    _session(db, other_daily, _local(2026, 1, 15, 14), "abandoned")
    running = WorkSession(
        daily_task_id=other_daily.id,
        started_at=_local(2026, 1, 15, 15).astimezone(UTC),
        session_state="running",
        outcome=None,
        planned_duration_seconds=1500,
    )
    db.add(running)
    db.commit()

    result = stuck_task_drilldown(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.total_stuck_sessions == 2
    assert result.total_distinct_stuck_tasks == 1
    assert result.returned_task_count == 1
    observation = result.tasks[0]
    assert observation.task_id == kept.id
    assert observation.title == "[HR] Await contract amendment"
    assert observation.stuck_session_count == 2
    assert observation.first_stuck_date == date(2026, 1, 15)
    assert observation.last_stuck_date == date(2026, 1, 15)
    assert FORBIDDEN.isdisjoint(observation.__dataclass_fields__)


def test_duplicate_task_appears_once_and_order_is_deterministic(
    db,
    make_task,
    make_daily_task,
):
    first = make_task("First repeated")
    second = make_task("Second repeated")
    later = make_task("Single later")
    first_a = make_daily_task(first, target_date=date(2026, 1, 12))
    first_b = make_daily_task(first, target_date=date(2026, 1, 14))
    second_daily = make_daily_task(
        second,
        target_date=date(2026, 1, 15),
    )
    later_daily = make_daily_task(
        later,
        target_date=date(2026, 1, 20),
    )
    _session(db, first_a, _local(2026, 1, 12), "stuck")
    _session(db, first_b, _local(2026, 1, 14), "stuck")
    _session(db, first_b, _local(2026, 1, 14, 11), "stuck")
    _session(db, second_daily, _local(2026, 1, 15), "stuck")
    _session(db, second_daily, _local(2026, 1, 15, 11), "stuck")
    _session(db, second_daily, _local(2026, 1, 15, 12), "stuck")
    _session(db, later_daily, _local(2026, 1, 20), "stuck")

    result = stuck_task_drilldown(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 20),
    )
    assert result.total_stuck_sessions == 7
    assert result.total_distinct_stuck_tasks == 3
    assert [row.task_id for row in result.tasks] == [
        second.id,
        first.id,
        later.id,
    ]
    assert result.tasks[0].stuck_session_count == 3
    assert result.tasks[0].last_stuck_date == date(2026, 1, 15)
    assert result.tasks[1].stuck_session_count == 3
    assert result.tasks[1].first_stuck_date == date(2026, 1, 12)
    assert result.tasks[1].last_stuck_date == date(2026, 1, 14)


def test_truncation_metadata_uses_limit(
    db,
    make_task,
    make_daily_task,
):
    for index in range(5):
        task = make_task(f"Stuck task {index}")
        daily = make_daily_task(
            task,
            target_date=date(2026, 1, 15),
        )
        _session(
            db,
            daily,
            _local(2026, 1, 15, 9 + index),
            "stuck",
        )
    result = stuck_task_drilldown(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
        limit=2,
    )
    defaulted = stuck_task_drilldown(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    assert result.limit == 2
    assert result.total_distinct_stuck_tasks == 5
    assert result.total_stuck_sessions == 5
    assert result.returned_task_count == 2
    assert len(result.tasks) == 2
    assert defaulted.limit == DEFAULT_LIMIT
    assert defaulted.returned_task_count == 5


def test_local_date_filter_uses_started_at(
    db,
    make_task,
    make_daily_task,
):
    task = make_task("Boundary")
    daily = make_daily_task(task, target_date=date(2026, 7, 15))
    utc_near_midnight = datetime(2026, 7, 14, 23, 30, tzinfo=UTC)
    _session(
        db,
        daily,
        utc_near_midnight.astimezone(LONDON),
        "stuck",
    )
    included = stuck_task_drilldown(
        db,
        from_date=date(2026, 7, 15),
        to_date=date(2026, 7, 15),
    )
    previous = stuck_task_drilldown(
        db,
        from_date=date(2026, 7, 14),
        to_date=date(2026, 7, 14),
    )
    assert included.total_stuck_sessions == 1
    assert included.tasks[0].first_stuck_date == date(2026, 7, 15)
    assert previous.total_stuck_sessions == 0
