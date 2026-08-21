from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import WorkSession
from app.services.review import get_completed_sessions_for_date
from app.time import UTC

LONDON = ZoneInfo("Europe/London")


def _london_to_utc(year, month, day, hour, minute=0, *, fold=0):
    local = datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=LONDON,
        fold=fold,
    )
    return local.astimezone(UTC)


def _add_completed_session(db, daily_task, started_at):
    work_session = WorkSession(
        daily_task_id=daily_task.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state="completed",
        outcome="progress",
    )
    db.add(work_session)
    db.commit()
    db.refresh(work_session)
    return work_session


def _session_ids_for_date(db, target_date):
    sessions = get_completed_sessions_for_date(
        db=db,
        target_date=target_date,
    )
    return [session.id for session in sessions]


def test_review_includes_ordinary_gmt_session(
    db,
    make_task,
    make_daily_task,
):
    local_date = date(2026, 1, 15)
    task = make_task("GMT work")
    daily_task = make_daily_task(task, target_date=local_date)
    work_session = _add_completed_session(
        db,
        daily_task,
        _london_to_utc(2026, 1, 15, 12, 0),
    )

    assert _session_ids_for_date(db, local_date) == [
        work_session.id
    ]
    assert _session_ids_for_date(db, date(2026, 1, 14)) == []
    assert _session_ids_for_date(db, date(2026, 1, 16)) == []


def test_review_includes_ordinary_bst_session(
    db,
    make_task,
    make_daily_task,
):
    local_date = date(2026, 7, 15)
    task = make_task("BST work")
    daily_task = make_daily_task(task, target_date=local_date)
    work_session = _add_completed_session(
        db,
        daily_task,
        _london_to_utc(2026, 7, 15, 12, 0),
    )

    assert work_session.started_at == datetime(
        2026, 7, 15, 11, 0, tzinfo=UTC
    )
    assert _session_ids_for_date(db, local_date) == [
        work_session.id
    ]
    assert _session_ids_for_date(db, date(2026, 7, 14)) == []


def test_bst_half_past_midnight_belongs_to_local_review_date(
    db,
    make_task,
    make_daily_task,
):
    local_date = date(2026, 7, 15)
    started_at = _london_to_utc(2026, 7, 15, 0, 30)

    assert started_at == datetime(
        2026, 7, 14, 23, 30, tzinfo=UTC
    )
    assert started_at.date() == date(2026, 7, 14)

    task = make_task("Late night")
    daily_task = make_daily_task(task, target_date=local_date)
    work_session = _add_completed_session(
        db,
        daily_task,
        started_at,
    )

    assert _session_ids_for_date(db, local_date) == [
        work_session.id
    ]
    assert _session_ids_for_date(db, date(2026, 7, 14)) == []


def test_review_respects_local_midnight_boundary(
    db,
    make_task,
    make_daily_task,
):
    before_task = make_task("Before midnight")
    after_task = make_task("After midnight")
    before_daily = make_daily_task(
        before_task,
        target_date=date(2026, 7, 14),
    )
    after_daily = make_daily_task(
        after_task,
        target_date=date(2026, 7, 15),
    )

    before = _add_completed_session(
        db,
        before_daily,
        _london_to_utc(2026, 7, 14, 23, 59),
    )
    after = _add_completed_session(
        db,
        after_daily,
        _london_to_utc(2026, 7, 15, 0, 0),
    )

    assert _session_ids_for_date(db, date(2026, 7, 14)) == [
        before.id
    ]
    assert _session_ids_for_date(db, date(2026, 7, 15)) == [
        after.id
    ]


def test_review_includes_both_sides_of_fall_back_overlap(
    db,
    make_task,
    make_daily_task,
):
    local_date = date(2026, 10, 25)
    task = make_task("Overlap hour")
    daily_task = make_daily_task(task, target_date=local_date)

    first = _add_completed_session(
        db,
        daily_task,
        _london_to_utc(2026, 10, 25, 1, 30, fold=0),
    )
    second = _add_completed_session(
        db,
        daily_task,
        _london_to_utc(2026, 10, 25, 1, 30, fold=1),
    )

    assert first.started_at == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    assert second.started_at == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )
    assert _session_ids_for_date(db, local_date) == [
        first.id,
        second.id,
    ]
    assert _session_ids_for_date(db, date(2026, 10, 24)) == []
    assert _session_ids_for_date(db, date(2026, 10, 26)) == []
