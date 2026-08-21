from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import DailyTask, Task


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


def add_task_to_day(
    db: Session,
    task: Task,
    target_date: date,
    planned_sessions: int | None = None,
) -> DailyTask:
    if task.status != "active":
        raise ValueError(
            "Only active tasks can be added to a day."
        )

    existing = (
        db.query(DailyTask)
        .filter(
            DailyTask.task_id == task.id,
            DailyTask.date == target_date,
        )
        .first()
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
    db.commit()
    db.refresh(daily_task)

    return daily_task


def remove_task_from_day(
    db: Session,
    daily_task: DailyTask,
) -> DailyTask:
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
    if planned_sessions < 1:
        raise ValueError(
            "Planned sessions must be at least 1."
        )

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