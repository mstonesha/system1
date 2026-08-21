import pytest

from app.models import Task
from app.services.tasks import (
    cancel_task,
    complete_task,
    create_task,
    update_task_title,
)


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


def test_create_task_rejects_empty_title(db):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        create_task(db, title="")

    assert db.query(Task).count() == 0


def test_create_task_rejects_whitespace_only_title(db):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        create_task(db, title="   \t\n  ")

    assert db.query(Task).count() == 0


def test_update_task_title_rejects_whitespace_only(db, make_task):
    task = make_task("Keep me")

    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        update_task_title(db, task, "   ")

    db.refresh(task)
    assert task.title == "Keep me"


def test_create_task_rejects_title_longer_than_200(db):
    with pytest.raises(
        ValueError,
        match="200 characters",
    ):
        create_task(db, title="a" * 201)

    assert db.query(Task).count() == 0


def test_update_task_title_rejects_title_longer_than_200(
    db,
    make_task,
):
    task = make_task("Short")

    with pytest.raises(
        ValueError,
        match="200 characters",
    ):
        update_task_title(db, task, "b" * 201)

    db.refresh(task)
    assert task.title == "Short"


def test_create_task_accepts_200_character_title(db):
    title = "c" * 200
    task = create_task(db, title=title)

    assert task.title == title
    assert len(task.title) == 200
