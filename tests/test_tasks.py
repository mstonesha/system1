import pytest

from app.services.tasks import cancel_task, complete_task


def test_complete_task_blocked_by_active_descendants(
    db,
    make_task,
):
    parent = make_task("Parent")
    make_task("Child", parent=parent)

    db.refresh(parent)

    with pytest.raises(
        ValueError,
        match="active subtasks",
    ):
        complete_task(db, parent)

    db.refresh(parent)
    assert parent.status == "active"
    assert parent.completed_at is None


def test_cancel_task_succeeds(db, make_task):
    task = make_task("Drop this")

    cancelled = cancel_task(db, task)

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None
