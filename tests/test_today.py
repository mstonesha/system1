import pytest

from app.models import DailyTask
from app.services.tasks import cancel_task, complete_task
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
    remove_task_from_day,
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
