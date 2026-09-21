import logging
from datetime import date, timedelta

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DailyTask, Task, WorkSession


logger = logging.getLogger(__name__)


def get_daily_tasks_for_date(
    db: Session,
    target_date: date,
) -> list[DailyTask]:
    """Return the executable queue for a date.

    A DailyTask is executable only when it is still planned and its
    underlying Task is active. Historical DailyTask rows are left in
    place; they simply drop out of this queue.
    """
    return (
        db.query(DailyTask)
        .join(DailyTask.task)
        .filter(
            DailyTask.date == target_date,
            DailyTask.state == "planned",
            Task.status == "active",
        )
        .order_by(DailyTask.sort_order, DailyTask.created_at)
        .all()
    )


def validate_planned_sessions(
    planned_sessions: int | None,
) -> None:
    if planned_sessions is not None and planned_sessions < 1:
        raise ValueError(
            "Planned sessions must be at least 1."
        )


def _find_daily_task(
    db: Session,
    task_id: int,
    target_date: date,
) -> DailyTask | None:
    return (
        db.query(DailyTask)
        .filter(
            DailyTask.task_id == task_id,
            DailyTask.date == target_date,
        )
        .first()
    )


def add_task_to_day(
    db: Session,
    task: Task,
    target_date: date,
    planned_sessions: int | None = None,
) -> DailyTask:
    validate_planned_sessions(planned_sessions)

    if task.status != "active":
        raise ValueError(
            "Only active tasks can be added to a day."
        )

    existing = _find_daily_task(
        db=db,
        task_id=task.id,
        target_date=target_date,
    )

    highest_sort_order = (
        db.query(DailyTask.sort_order)
        .filter(
            DailyTask.date == target_date,
            DailyTask.state == "planned",
        )
        .order_by(DailyTask.sort_order.desc())
        .first()
    )

    next_sort_order = (
        highest_sort_order[0] + 1
        if highest_sort_order is not None
        else 0
    )

    if existing is not None:
        if existing.state == "planned":
            raise ValueError(
                "This task is already on the selected day."
            )

        if existing.state == "removed":
            existing.state = "planned"
            existing.planned_sessions = planned_sessions
            existing.sort_order = next_sort_order

            db.commit()
            db.refresh(existing)

            return existing

        raise ValueError(
            "This task has already been concluded for the selected day."
        )

    daily_task = DailyTask(
        task_id=task.id,
        date=target_date,
        planned_sessions=planned_sessions,
        state="planned",
        sort_order=next_sort_order,
    )

    db.add(daily_task)

    try:
        db.commit()
        db.refresh(daily_task)
    except IntegrityError as exc:
        db.rollback()

        if isinstance(exc.orig, UniqueViolation):
            logger.warning(
                "Concurrent add-to-day rejected "
                "by unique constraint "
                "(task_id=%s)",
                task.id,
            )
            raise ValueError(
                "This task is already on the selected day."
            ) from exc

        raise

    return daily_task


def remove_task_from_day(
    db: Session,
    daily_task: DailyTask,
) -> DailyTask:
    running_session = (
        db.query(WorkSession)
        .filter(
            WorkSession.daily_task_id == daily_task.id,
            WorkSession.session_state == "running",
        )
        .first()
    )

    if running_session is not None:
        raise ValueError(
            "Cannot remove a task from the day while a work session is running."
        )

    daily_task.state = "removed"

    remaining_tasks = (
        db.query(DailyTask)
        .filter(
            DailyTask.date == daily_task.date,
            DailyTask.state == "planned",
            DailyTask.id != daily_task.id,
        )
        .order_by(
            DailyTask.sort_order,
            DailyTask.created_at,
        )
        .all()
    )

    for index, item in enumerate(remaining_tasks):
        item.sort_order = index

    db.commit()
    db.refresh(daily_task)

    return daily_task

def update_planned_sessions(
    db: Session,
    daily_task: DailyTask,
    planned_sessions: int,
) -> DailyTask:
    validate_planned_sessions(planned_sessions)

    daily_task.planned_sessions = planned_sessions

    db.commit()
    db.refresh(daily_task)

    return daily_task

def move_daily_task(
    db: Session,
    daily_task: DailyTask,
    direction: str,
) -> DailyTask:
    if direction not in {"up", "down"}:
        raise ValueError(
            "Direction must be 'up' or 'down'."
        )

    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=daily_task.date,
    )

    current_index = next(
        (
            index
            for index, item in enumerate(daily_tasks)
            if item.id == daily_task.id
        ),
        None,
    )

    if current_index is None:
        raise ValueError(
            "Daily task could not be found in this day's plan."
        )

    if direction == "up":
        target_index = current_index - 1
    else:
        target_index = current_index + 1

    if target_index < 0 or target_index >= len(daily_tasks):
        return daily_task

    daily_tasks[current_index], daily_tasks[target_index] = (
        daily_tasks[target_index],
        daily_tasks[current_index],
    )

    for index, item in enumerate(daily_tasks):
        item.sort_order = index

    db.commit()
    db.refresh(daily_task)

    return daily_task


def reorder_daily_tasks(
    db: Session,
    target_date: date,
    ordered_ids: list[int],
) -> list[DailyTask]:
    """Replace executable queue order with a full permutation.

    ``ordered_ids`` must be the complete set of DailyTask ids
    returned by ``get_daily_tasks_for_date`` for ``target_date``,
    each exactly once, in the desired display order. On success,
    those rows are rewritten to dense ``sort_order`` 0..n-1.
    Other dates, non-executable rows, state, and planned_sessions
    are left unchanged.
    """
    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )
    expected_ids = [
        daily_task.id
        for daily_task in daily_tasks
    ]

    if (
        len(ordered_ids) != len(set(ordered_ids))
        or set(ordered_ids) != set(expected_ids)
    ):
        raise ValueError(
            "Queue order must include each planned task "
            "for this day exactly once."
        )

    by_id = {
        daily_task.id: daily_task
        for daily_task in daily_tasks
    }

    for index, daily_task_id in enumerate(ordered_ids):
        by_id[daily_task_id].sort_order = index

    db.commit()

    return get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )


def get_carry_forward_candidates(
    db: Session,
    target_date: date,
) -> list[DailyTask]:
    previous_date = target_date - timedelta(days=1)

    previous_day_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=previous_date,
    )

    current_day_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )

    current_task_ids = {
        daily_task.task_id
        for daily_task in current_day_tasks
    }

    candidates = [
        daily_task
        for daily_task in previous_day_tasks
        if daily_task.task.status == "active"
        and daily_task.task_id not in current_task_ids
    ]

    return candidates


def format_minutes(total_minutes: int) -> str:
    """Render a minute count as a compact duration label."""
    if total_minutes < 60:
        return f"{total_minutes} min"

    hours, minutes = divmod(total_minutes, 60)
    hour_label = "1 hr" if hours == 1 else f"{hours} hr"

    if minutes == 0:
        return hour_label

    return f"{hour_label} {minutes} min"


def plan_display_totals(
    daily_tasks: list[DailyTask],
    *,
    focus_session_minutes: int,
    break_duration_minutes: int,
) -> dict[str, int | str]:
    """Presentation totals for the Today queue.

    NULL planned_sessions are omitted, not treated as zero or one.
    Inter-session breaks are (n - 1) configured breaks for n > 1
    explicit sessions; that is display-only.
    """
    planned_sessions = sum(
        daily_task.planned_sessions
        for daily_task in daily_tasks
        if daily_task.planned_sessions is not None
    )
    focus_minutes = (
        planned_sessions * focus_session_minutes
    )
    break_minutes = (
        (planned_sessions - 1) * break_duration_minutes
        if planned_sessions > 1
        else 0
    )
    including_breaks_minutes = (
        focus_minutes + break_minutes
    )

    return {
        "planned_sessions": planned_sessions,
        "focus_minutes": focus_minutes,
        "focus_label": format_minutes(focus_minutes),
        "break_minutes": break_minutes,
        "including_breaks_minutes":
            including_breaks_minutes,
        "including_breaks_label": format_minutes(
            including_breaks_minutes
        ),
    }