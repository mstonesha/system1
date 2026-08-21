import pytest

from app.models import DailyTask
from app.services.sessions import commit_work_session, start_work_session
from app.services.tasks import cancel_task, complete_task
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
    remove_task_from_day,
    update_planned_sessions,
)
from app.time import today


def test_removed_daily_task_can_be_replanned(
    db,
    make_task,
):
    task = make_task("Carry me")
    target_date = today()

    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    remove_task_from_day(db, daily_task)

    replanned = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=3,
    )

    assert replanned.id == daily_task.id
    assert replanned.state == "planned"
    assert replanned.planned_sessions == 3


@pytest.mark.parametrize(
    "concluded_state",
    ["completed", "abandoned"],
)
def test_concluded_daily_task_cannot_be_replanned(
    db,
    make_task,
    make_daily_task,
    concluded_state,
):
    task = make_task("Already finished")
    target_date = today()

    make_daily_task(
        task,
        target_date=target_date,
        state=concluded_state,
    )

    with pytest.raises(
        ValueError,
        match="already been concluded",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
        )


@pytest.mark.parametrize(
    "conclude",
    [complete_task, cancel_task],
)
def test_task_list_conclusion_drops_daily_task_from_executable_queue(
    db,
    make_task,
    conclude,
):
    task = make_task("On today")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    conclude(db, task)

    executable = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )
    assert executable == []

    preserved = db.get(DailyTask, daily_task.id)
    assert preserved is not None
    assert preserved.state == "planned"


@pytest.mark.parametrize("planned_sessions", [0, -1, -3])
def test_add_task_to_day_rejects_non_positive_planned_sessions(
    db,
    make_task,
    planned_sessions,
):
    task = make_task("Needs a plan")
    target_date = today()

    with pytest.raises(
        ValueError,
        match="at least 1",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
            planned_sessions=planned_sessions,
        )

    assert db.query(DailyTask).count() == 0


def test_add_task_to_day_allows_uncommitted_planned_sessions(
    db,
    make_task,
):
    task = make_task("No count yet")
    target_date = today()

    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=None,
    )

    assert daily_task.planned_sessions is None
    assert daily_task.state == "planned"


@pytest.mark.parametrize("planned_sessions", [0, -1])
def test_update_planned_sessions_rejects_non_positive_count(
    db,
    make_task,
    planned_sessions,
):
    task = make_task("Already planned")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=2,
    )

    with pytest.raises(
        ValueError,
        match="at least 1",
    ):
        update_planned_sessions(
            db=db,
            daily_task=daily_task,
            planned_sessions=planned_sessions,
        )

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 2


def test_cannot_remove_daily_task_with_running_work_session(
    db,
    make_task,
    make_work_session,
):
    task = make_task("In progress")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    work_session = make_work_session(daily_task)

    with pytest.raises(
        ValueError,
        match="work session is running",
    ):
        remove_task_from_day(db, daily_task)

    db.refresh(daily_task)
    db.refresh(work_session)
    assert daily_task.state == "planned"
    assert work_session.session_state == "running"
    assert work_session.ended_at is None
    assert work_session.outcome is None


def test_can_remove_daily_task_after_running_session_is_committed(
    db,
    make_task,
):
    task = make_task("Done for now")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )

    commit_work_session(
        db=db,
        work_session=work_session,
        outcome="progress",
    )

    removed = remove_task_from_day(db, daily_task)

    assert removed.state == "removed"
    db.refresh(work_session)
    assert work_session.session_state == "completed"
    assert work_session.outcome == "progress"


def test_add_task_to_day_unique_constraint_keeps_session_usable(
    db,
    make_task,
    monkeypatch,
):
    task = make_task("Once only")
    target_date = today()
    first = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    first_id = first.id

    monkeypatch.setattr(
        "app.services.today._find_daily_task",
        lambda db, task_id, target_date: None,
    )

    with pytest.raises(
        ValueError,
        match="already on the selected day",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
            planned_sessions=1,
        )

    persisted = db.get(DailyTask, first_id)
    assert persisted is not None
    assert persisted.state == "planned"

    matching = (
        db.query(DailyTask)
        .filter(
            DailyTask.task_id == task.id,
            DailyTask.date == target_date,
        )
        .count()
    )
    assert matching == 1

