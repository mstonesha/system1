from datetime import date

from sqlalchemy.orm import Session

from app.models import DailyTask, Task


def get_daily_tasks_for_date(
    db: Session,
    target_date: date,
) -> list[DailyTask]:
    return (
        db.query(DailyTask)
        .filter(DailyTask.date == target_date)
        .order_by(DailyTask.sort_order, DailyTask.created_at)
        .all()
    )


def add_task_to_day(
    db: Session,
    task: Task,
    target_date: date,
    planned_sessions: int | None = None,
) -> DailyTask:
    existing = (
        db.query(DailyTask)
        .filter(
            DailyTask.task_id == task.id,
            DailyTask.date == target_date,
        )
        .first()
    )

    if existing is not None:
        raise ValueError(
            "This task is already on the selected day."
        )

    if task.status != "active":
        raise ValueError(
            "Only active tasks can be added to a day."
        )

    highest_sort_order = (
        db.query(DailyTask.sort_order)
        .filter(DailyTask.date == target_date)
        .order_by(DailyTask.sort_order.desc())
        .first()
    )

    next_sort_order = (
        highest_sort_order[0] + 1
        if highest_sort_order is not None
        else 0
    )

    daily_task = DailyTask(
        task_id=task.id,
        date=target_date,
        planned_sessions=planned_sessions,
        sort_order=next_sort_order,
    )

    db.add(daily_task)
    db.commit()
    db.refresh(daily_task)

    return daily_task


def remove_task_from_day(
    db: Session,
    daily_task: DailyTask,
) -> None:
    db.delete(daily_task)
    db.commit()