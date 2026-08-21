from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import DailyTask, WorkSession
from app.services.tasks import cancel_task, complete_task
from app.services.today import get_daily_tasks_for_date


ALLOWED_OUTCOMES = {
    "progress",
    "complete",
    "stuck",
    "paused",
    "abandoned",
}


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


def move_daily_task_to_bottom(
    db: Session,
    daily_task: DailyTask,
) -> None:
    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=daily_task.date,
    )

    reordered_tasks = [
        item
        for item in daily_tasks
        if item.id != daily_task.id
    ]

    reordered_tasks.append(daily_task)

    for index, item in enumerate(reordered_tasks):
        item.sort_order = index


def renumber_daily_queue(
    db: Session,
    target_date: date,
) -> None:
    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )

    for index, item in enumerate(daily_tasks):
        item.sort_order = index


def end_work_session_early(
    db: Session,
    work_session: WorkSession,
) -> WorkSession:
    if work_session.session_state != "running":
        raise ValueError(
            "This work session is no longer running."
        )

    if work_session.ended_at is not None:
        return work_session

    now = datetime.utcnow()

    planned_end_at = (
        work_session.started_at
        + timedelta(
            seconds=work_session.planned_duration_seconds
        )
    )

    work_session.ended_at = min(
        now,
        planned_end_at,
    )

    db.commit()
    db.refresh(work_session)

    return work_session
    

def commit_work_session(
    db: Session,
    work_session: WorkSession,
    outcome: str,
    interrupted: bool = False,
    note: str | None = None,
) -> WorkSession:
    outcome = outcome.strip().lower()

    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError(
            "Invalid session outcome."
        )

    if work_session.session_state != "running":
        raise ValueError(
            "This work session has already been committed."
        )

    cleaned_note = (
        note.strip()
        if note is not None
        else ""
    )

    if len(cleaned_note) > 64:
        raise ValueError(
            "Session note cannot exceed 64 characters."
        )

    daily_task = work_session.daily_task
    task = daily_task.task

    planned_end_at = (
        work_session.started_at
        + timedelta(
            seconds=work_session.planned_duration_seconds
        )
    )

    recorded_end_at = (
        work_session.ended_at
        if work_session.ended_at is not None
        else datetime.utcnow()
    )

    ended_at = min(
        recorded_end_at,
        planned_end_at,
    )

    actual_duration_seconds = max(
        0,
        int(
            (
                ended_at
                - work_session.started_at
            ).total_seconds()
        ),
    )

    if outcome == "complete":
        complete_task(
            db=db,
            task=task,
        )

        daily_task.state = "completed"

    elif outcome == "abandoned":
        cancel_task(
            db=db,
            task=task,
        )

        daily_task.state = "abandoned"

    elif outcome in {"stuck", "paused"}:
        move_daily_task_to_bottom(
            db=db,
            daily_task=daily_task,
        )

    work_session.ended_at = ended_at
    work_session.actual_duration_seconds = (
        actual_duration_seconds
    )
    work_session.session_state = "completed"
    work_session.outcome = outcome
    work_session.interrupted = interrupted
    work_session.note = cleaned_note or None

    if outcome in {"complete", "abandoned"}:
        renumber_daily_queue(
            db=db,
            target_date=daily_task.date,
        )

    db.commit()
    db.refresh(work_session)

    return work_session