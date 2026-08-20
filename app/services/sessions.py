from datetime import date

from sqlalchemy.orm import Session

from app.models import DailyTask, WorkSession
from app.services.today import get_daily_tasks_for_date


def get_running_session(
    db: Session,
) -> WorkSession | None:
    return (
        db.query(WorkSession)
        .filter(WorkSession.session_state == "running")
        .order_by(WorkSession.started_at.desc())
        .first()
    )


def get_next_daily_task(
    db: Session,
    target_date: date,
) -> DailyTask | None:
    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )

    if not daily_tasks:
        return None

    return daily_tasks[0]


def start_work_session(
    db: Session,
    target_date: date,
    duration_minutes: int = 25,
) -> WorkSession:
    if duration_minutes < 1:
        raise ValueError(
            "Session duration must be at least 1 minute."
        )

    running_session = get_running_session(db)

    if running_session is not None:
        raise ValueError(
            "A work session is already running."
        )

    daily_task = get_next_daily_task(
        db=db,
        target_date=target_date,
    )

    if daily_task is None:
        raise ValueError(
            "There are no planned tasks for the selected day."
        )

    work_session = WorkSession(
        daily_task_id=daily_task.id,
        planned_duration_seconds=duration_minutes * 60,
        session_state="running",
    )

    db.add(work_session)
    db.commit()
    db.refresh(work_session)

    return work_session