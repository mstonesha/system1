from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DailyTask, Task, WorkSession
from app.services.sessions import (
    commit_work_session,
    end_work_session_early,
    get_next_daily_task,
    start_work_session,
)
from app.services.tasks import cancel_task, complete_task
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
)
from app.time import today, utc_now


def _plan_task(db, make_task, title, target_date):
    task = make_task(title)
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    return task, daily_task


@pytest.mark.parametrize(
    "outcome",
    ["stuck", "paused"],
)
def test_stuck_and_paused_move_daily_task_to_bottom(
    db,
    make_task,
    outcome,
):
    target_date = today()
    first_task, first_daily = _plan_task(
        db,
        make_task,
        "First",
        target_date,
    )
    _, second_daily = _plan_task(
        db,
        make_task,
        "Second",
        target_date,
    )

    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )
    assert work_session.daily_task_id == first_daily.id

    commit_work_session(
        db=db,
        work_session=work_session,
        outcome=outcome,
    )

    db.refresh(first_task)
    db.refresh(first_daily)
    db.refresh(second_daily)

    planned = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )

    assert [item.id for item in planned] == [
        second_daily.id,
        first_daily.id,
    ]
    assert first_task.status == "active"
    assert first_daily.state == "planned"


def test_start_work_session_uses_configured_focus_duration(
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    _plan_task(db, make_task, "Focus", today())

    work_session = start_work_session(
        db=db,
        target_date=today(),
    )

    assert work_session.planned_duration_seconds == 40 * 60


@pytest.mark.parametrize(
    "duration_minutes",
    [0, -5],
)
def test_zero_and_negative_session_duration_rejected(
    db,
    duration_minutes,
):
    with pytest.raises(
        ValueError,
        match="at least 1 minute",
    ):
        start_work_session(
            db=db,
            target_date=today(),
            duration_minutes=duration_minutes,
        )


def test_invalid_work_session_outcome_rejected(
    db,
    make_task,
):
    _plan_task(db, make_task, "Focus", today())
    work_session = start_work_session(
        db=db,
        target_date=today(),
    )

    with pytest.raises(
        ValueError,
        match="Invalid session outcome",
    ):
        commit_work_session(
            db=db,
            work_session=work_session,
            outcome="finished",
        )


def test_note_over_64_characters_rejected(
    db,
    make_task,
):
    _plan_task(db, make_task, "Focus", today())
    work_session = start_work_session(
        db=db,
        target_date=today(),
    )

    with pytest.raises(
        ValueError,
        match="cannot exceed 64 characters",
    ):
        commit_work_session(
            db=db,
            work_session=work_session,
            outcome="progress",
            note="n" * 65,
        )


def test_end_early_then_commit_records_duration_to_early_end(
    db,
    make_task,
):
    target_date = today()
    _plan_task(db, make_task, "Focus", target_date)

    work_session = start_work_session(
        db=db,
        target_date=target_date,
        duration_minutes=25,
    )
    work_session.started_at = (
        utc_now() - timedelta(minutes=10)
    )
    db.commit()
    db.refresh(work_session)

    work_session = end_work_session_early(
        db=db,
        work_session=work_session,
    )
    ended_at = work_session.ended_at
    assert ended_at is not None
    assert work_session.started_at.tzinfo is not None
    assert ended_at.tzinfo is not None

    planned_end_at = (
        work_session.started_at
        + timedelta(minutes=25)
    )
    assert ended_at < planned_end_at

    work_session = commit_work_session(
        db=db,
        work_session=work_session,
        outcome="progress",
    )

    expected_seconds = int(
        (ended_at - work_session.started_at).total_seconds()
    )
    assert work_session.actual_duration_seconds == expected_seconds
    assert work_session.ended_at == ended_at
    assert (
        work_session.actual_duration_seconds
        < work_session.planned_duration_seconds
    )
    assert 9 * 60 <= work_session.actual_duration_seconds <= 11 * 60


def test_second_sequential_session_start_rejected_while_running(
    db,
    make_task,
):
    target_date = today()
    _plan_task(db, make_task, "Focus", target_date)

    start_work_session(
        db=db,
        target_date=target_date,
    )

    with pytest.raises(
        ValueError,
        match="already running",
    ):
        start_work_session(
            db=db,
            target_date=target_date,
        )


def test_commit_complete_persists_task_daily_task_and_session(
    db,
    engine,
    make_task,
):
    target_date = today()
    task, daily_task = _plan_task(
        db,
        make_task,
        "Finish me",
        target_date,
    )
    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )

    commit_work_session(
        db=db,
        work_session=work_session,
        outcome="complete",
    )

    with Session(engine) as committed:
        persisted_task = committed.get(Task, task.id)
        persisted_daily = committed.get(
            DailyTask,
            daily_task.id,
        )
        persisted_session = committed.get(
            WorkSession,
            work_session.id,
        )

        assert persisted_task.status == "completed"
        assert persisted_task.completed_at is not None
        assert persisted_daily.state == "completed"
        assert persisted_session.session_state == "completed"
        assert persisted_session.outcome == "complete"


def test_commit_abandoned_persists_task_daily_task_and_session(
    db,
    engine,
    make_task,
):
    target_date = today()
    task, daily_task = _plan_task(
        db,
        make_task,
        "Drop me",
        target_date,
    )
    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )

    commit_work_session(
        db=db,
        work_session=work_session,
        outcome="abandoned",
    )

    with Session(engine) as committed:
        persisted_task = committed.get(Task, task.id)
        persisted_daily = committed.get(
            DailyTask,
            daily_task.id,
        )
        persisted_session = committed.get(
            WorkSession,
            work_session.id,
        )

        assert persisted_task.status == "cancelled"
        assert persisted_task.completed_at is None
        assert persisted_daily.state == "abandoned"
        assert persisted_session.session_state == "completed"
        assert persisted_session.outcome == "abandoned"


def test_commit_complete_rolls_back_if_later_step_fails(
    db,
    engine,
    make_task,
    monkeypatch,
):
    target_date = today()
    task, daily_task = _plan_task(
        db,
        make_task,
        "Keep me",
        target_date,
    )
    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )

    def fail_renumber(*args, **kwargs):
        raise RuntimeError("queue renumber failed")

    monkeypatch.setattr(
        "app.services.sessions.renumber_daily_queue",
        fail_renumber,
    )

    with pytest.raises(
        RuntimeError,
        match="queue renumber failed",
    ):
        commit_work_session(
            db=db,
            work_session=work_session,
            outcome="complete",
        )

    with Session(engine) as committed:
        persisted_task = committed.get(Task, task.id)
        persisted_daily = committed.get(
            DailyTask,
            daily_task.id,
        )
        persisted_session = committed.get(
            WorkSession,
            work_session.id,
        )

        assert persisted_task.status == "active"
        assert persisted_task.completed_at is None
        assert persisted_daily.state == "planned"
        assert persisted_session.session_state == "running"
        assert persisted_session.outcome is None


@pytest.mark.parametrize(
    "conclude",
    [complete_task, cancel_task],
)
def test_start_work_session_refuses_when_only_task_is_concluded(
    db,
    make_task,
    conclude,
):
    target_date = today()
    task, _daily_task = _plan_task(
        db,
        make_task,
        "Only item",
        target_date,
    )

    conclude(db, task)

    with pytest.raises(
        ValueError,
        match="no planned tasks",
    ):
        start_work_session(
            db=db,
            target_date=target_date,
        )


def test_timer_skips_concluded_first_daily_task_for_later_active(
    db,
    make_task,
):
    target_date = today()
    first_task, _first_daily = _plan_task(
        db,
        make_task,
        "First",
        target_date,
    )
    _second_task, second_daily = _plan_task(
        db,
        make_task,
        "Second",
        target_date,
    )

    complete_task(db, first_task)

    next_daily = get_next_daily_task(
        db=db,
        target_date=target_date,
    )
    assert next_daily is not None
    assert next_daily.id == second_daily.id

    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )
    assert work_session.daily_task_id == second_daily.id


def test_database_rejects_two_running_work_sessions(
    db,
    make_task,
):
    target_date = today()
    _task, daily_task = _plan_task(
        db,
        make_task,
        "Focus",
        target_date,
    )

    first = WorkSession(
        daily_task_id=daily_task.id,
        planned_duration_seconds=1500,
        session_state="running",
    )
    db.add(first)
    db.commit()

    second = WorkSession(
        daily_task_id=daily_task.id,
        planned_duration_seconds=1500,
        session_state="running",
    )
    db.add(second)

    with pytest.raises(IntegrityError):
        db.commit()

    db.rollback()

    running_count = (
        db.query(WorkSession)
        .filter(WorkSession.session_state == "running")
        .count()
    )
    assert running_count == 1
    assert db.get(DailyTask, daily_task.id) is not None


def test_start_work_session_constraint_race_keeps_session_usable(
    db,
    make_task,
    monkeypatch,
):
    target_date = today()
    _plan_task(db, make_task, "Focus", target_date)

    first = start_work_session(
        db=db,
        target_date=target_date,
    )
    first_id = first.id

    monkeypatch.setattr(
        "app.services.sessions.get_running_session",
        lambda db: None,
    )

    with pytest.raises(
        ValueError,
        match="already running",
    ):
        start_work_session(
            db=db,
            target_date=target_date,
        )

    persisted = db.get(WorkSession, first_id)
    assert persisted is not None
    assert persisted.session_state == "running"

    running_count = (
        db.query(WorkSession)
        .filter(WorkSession.session_state == "running")
        .count()
    )
    assert running_count == 1


def test_can_start_session_after_running_session_completed(
    db,
    make_task,
):
    target_date = today()
    _plan_task(db, make_task, "Focus", target_date)

    first = start_work_session(
        db=db,
        target_date=target_date,
    )

    commit_work_session(
        db=db,
        work_session=first,
        outcome="progress",
    )

    second = start_work_session(
        db=db,
        target_date=target_date,
    )

    assert second.id != first.id
    assert second.session_state == "running"

    running = (
        db.query(WorkSession)
        .filter(WorkSession.session_state == "running")
        .all()
    )
    assert [session.id for session in running] == [second.id]



