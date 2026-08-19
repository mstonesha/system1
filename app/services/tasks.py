from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Task


def get_root_tasks(db: Session) -> list[Task]:
    return (
        db.query(Task)
        .filter(Task.parent_task_id.is_(None))
        .order_by(Task.sort_order, Task.created_at)
        .all()
    )


def has_active_descendants(task: Task) -> bool:
    for child in task.children:
        if child.status == "active":
            return True

        if has_active_descendants(child):
            return True

    return False


def create_task(
    db: Session,
    title: str,
    parent_task_id: int | None = None,
) -> Task:
    task = Task(
        title=title.strip(),
        parent_task_id=parent_task_id,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


def complete_task(
    db: Session,
    task: Task,
) -> Task:
    if has_active_descendants(task):
        raise ValueError(
            "Cannot complete this task while it has active subtasks."
        )

    task.status = "completed"
    task.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(task)

    return task


def cancel_task(
    db: Session,
    task: Task,
) -> Task:
    task.status = "cancelled"
    task.completed_at = None

    db.commit()
    db.refresh(task)

    return task


def reopen_task(
    db: Session,
    task: Task,
) -> Task:
    task.status = "active"
    task.completed_at = None

    db.commit()
    db.refresh(task)

    return task


def update_task_title(
    db: Session,
    task: Task,
    title: str,
) -> Task:
    task.title = title.strip()

    db.commit()
    db.refresh(task)

    return task