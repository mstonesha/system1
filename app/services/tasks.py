from sqlalchemy.orm import Session

from app.models import Task
from app.time import utc_now


MAX_TASK_TITLE_LENGTH = 200
TASK_PATH_SEPARATOR = " › "

DEFAULT_TASK_AREA = "general"
ALL_AREAS_FILTER = "all"
TASK_AREAS = (
    "general",
    "work",
    "home",
    "development",
)
ALLOWED_TASK_AREAS = frozenset(TASK_AREAS)

OLDEST_TASK_SORT = "oldest"
NEWEST_TASK_SORT = "newest"
TASK_SORTS = (
    OLDEST_TASK_SORT,
    NEWEST_TASK_SORT,
)
ALLOWED_TASK_SORTS = frozenset(TASK_SORTS)
TASK_SORT_LABELS = {
    OLDEST_TASK_SORT: "Oldest first",
    NEWEST_TASK_SORT: "Newest first",
}


def normalize_task_sort(sort: str | None) -> str:
    if sort is None:
        return OLDEST_TASK_SORT

    cleaned = sort.strip().lower()

    if cleaned == "":
        return OLDEST_TASK_SORT

    if cleaned not in ALLOWED_TASK_SORTS:
        raise ValueError(
            "Invalid task sort."
        )

    return cleaned


def task_sort_label(sort: str) -> str:
    return TASK_SORT_LABELS[sort]


def sort_task_siblings(
    tasks: list[Task],
    sort: str,
) -> list[Task]:
    """Return a new sibling list in display order.

    Does not mutate the input or any ORM relationship.
    """
    return sorted(
        list(tasks),
        key=lambda task: (task.created_at, task.id),
        reverse=sort == NEWEST_TASK_SORT,
    )


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


def task_area_label(area: str) -> str:
    return area.replace("-", " ").title()


def normalize_task_area(area: str) -> str:
    cleaned = area.strip().lower()

    if cleaned not in ALLOWED_TASK_AREAS:
        raise ValueError(
            "Invalid task area."
        )

    return cleaned


def normalize_area_filter(area: str | None) -> str | None:
    if area is None:
        return None

    cleaned = area.strip().lower()

    if cleaned in {"", ALL_AREAS_FILTER}:
        return None

    if cleaned not in ALLOWED_TASK_AREAS:
        raise ValueError(
            "Invalid task area."
        )

    return cleaned


def filter_task_tree_by_area(
    nodes: list[dict],
    area: str | None,
) -> list[dict]:
    if area is None:
        return nodes

    filtered: list[dict] = []

    for node in nodes:
        children = filter_task_tree_by_area(
            node["children"],
            area,
        )

        if node["task"].area == area or children:
            filtered.append(
                {
                    "task": node["task"],
                    "children": children,
                }
            )

    return filtered


def _area_for_new_task(
    db: Session,
    parent_task_id: int | None,
    area: str | None,
) -> str:
    if area is not None and area.strip():
        return normalize_task_area(area)

    if parent_task_id is not None:
        parent = db.get(Task, parent_task_id)
        if parent is not None:
            return parent.area

    return DEFAULT_TASK_AREA


def create_task(
    db: Session,
    title: str,
    parent_task_id: int | None = None,
    area: str | None = None,
) -> Task:
    task = Task(
        title=normalize_task_title(title),
        parent_task_id=parent_task_id,
        area=_area_for_new_task(
            db,
            parent_task_id,
            area,
        ),
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


def update_task_area(
    db: Session,
    task: Task,
    area: str,
) -> Task:
    task.area = normalize_task_area(area)

    db.commit()
    db.refresh(task)

    return task


def update_task(
    db: Session,
    task: Task,
    title: str,
    area: str | None = None,
) -> Task:
    new_title = normalize_task_title(title)
    new_area = None

    if area is not None and area.strip():
        new_area = normalize_task_area(area)

    task.title = new_title

    if new_area is not None:
        task.area = new_area

    db.commit()
    db.refresh(task)

    return task