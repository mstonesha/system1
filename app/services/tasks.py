from sqlalchemy.orm import Session

from app.models import Task
from app.time import utc_now


MAX_TASK_TITLE_LENGTH = 200
TASK_PATH_SEPARATOR = " › "


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


def task_ancestor_titles(task: Task) -> list[str]:
    """Return ancestor titles from the root down to the parent.

    Presentation only. Does not change parent/child semantics.
    """
    titles: list[str] = []
    seen: set[int] = set()
    current = task.parent

    while current is not None:
        if current.id in seen:
            break

        seen.add(current.id)
        titles.append(current.title)
        current = current.parent

    titles.reverse()
    return titles


def task_ancestry_label(task: Task) -> str:
    return TASK_PATH_SEPARATOR.join(
        task_ancestor_titles(task)
    )


def task_breadcrumb(task: Task) -> str:
    titles = [
        *task_ancestor_titles(task),
        task.title,
    ]
    return TASK_PATH_SEPARATOR.join(titles)


def normalize_task_title(title: str) -> str:
    cleaned = title.strip()

    if not cleaned:
        raise ValueError(
            "Task title cannot be empty."
        )

    if len(cleaned) > MAX_TASK_TITLE_LENGTH:
        raise ValueError(
            "Task title cannot exceed 200 characters."
        )

    return cleaned


def create_task(
    db: Session,
    title: str,
    parent_task_id: int | None = None,
) -> Task:
    task = Task(
        title=normalize_task_title(title),
        parent_task_id=parent_task_id,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


def mark_task_completed(task: Task) -> None:
    if has_active_descendants(task):
        raise ValueError(
            "Cannot complete this task while it has active subtasks."
        )

    task.status = "completed"
    task.completed_at = utc_now()


def mark_task_cancelled(task: Task) -> None:
    task.status = "cancelled"
    task.completed_at = None


def complete_task(
    db: Session,
    task: Task,
) -> Task:
    mark_task_completed(task)

    db.commit()
    db.refresh(task)

    return task


def cancel_task(
    db: Session,
    task: Task,
) -> Task:
    mark_task_cancelled(task)

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
    task.title = normalize_task_title(title)

    db.commit()
    db.refresh(task)

    return task