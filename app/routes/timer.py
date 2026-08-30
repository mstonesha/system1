from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.http import require_browser_session
from app.config import get_settings
from app.database import get_db
from app.models import DailyTask, WorkSession
from app.services.sessions import (
    commit_work_session,
    end_work_session_early,
    get_next_daily_task,
    get_running_session,
    start_work_session,
)
from app.templating import templates
from app.time import today, utc_now


router = APIRouter(
    prefix="/timer",
    tags=["timer"],
    dependencies=[Depends(require_browser_session)],
)


def _completed_session_count(
    db: Session,
    daily_task: DailyTask,
) -> int:
    return (
        db.query(WorkSession)
        .filter(
            WorkSession.daily_task_id == daily_task.id,
            WorkSession.session_state == "completed",
        )
        .count()
    )


def _planned_session_progress(
    db: Session,
    daily_task: DailyTask | None,
) -> dict:
    """Return Session N of M only when planned_sessions is set.

    Position is completed WorkSessions + 1. Remaining after the
    current/upcoming session is planned minus that position.
    If more sessions have been completed than planned, omit the
    position rather than inventing a larger total.
    """
    empty = {
        "session_number": None,
        "session_total": None,
        "remaining_after": None,
    }

    if (
        daily_task is None
        or daily_task.planned_sessions is None
    ):
        return empty

    session_number = (
        _completed_session_count(db, daily_task) + 1
    )
    session_total = daily_task.planned_sessions

    if session_number > session_total:
        return empty

    return {
        "session_number": session_number,
        "session_total": session_total,
        "remaining_after": (
            session_total - session_number
        ),
    }


@router.get("/")
def timer_page(
    request: Request,
    break_until: int | None = None,
    db: Session = Depends(get_db),
):
    selected_date = today()

    running_session = get_running_session(db)

    active_break_until = None

    if running_session is None and break_until is not None:
        now_timestamp = int(
            utc_now().timestamp()
        )

        if break_until > now_timestamp:
            active_break_until = break_until

    next_daily_task = None

    if (
        running_session is None
        and active_break_until is None
    ):
        next_daily_task = get_next_daily_task(
            db=db,
            target_date=selected_date,
        )

    session_daily_task = None
    remaining_after = None
    up_next_mode = None
    up_next_daily_task = None

    if running_session is not None:
        session_daily_task = running_session.daily_task

    elif active_break_until is not None:
        up_next_daily_task = get_next_daily_task(
            db=db,
            target_date=selected_date,
        )
        if up_next_daily_task is None:
            up_next_mode = "none"
        else:
            up_next_mode = "queued_task"
            session_daily_task = up_next_daily_task

    elif next_daily_task is not None:
        session_daily_task = next_daily_task

    progress = _planned_session_progress(
        db,
        session_daily_task,
    )
    session_number = progress["session_number"]
    session_total = progress["session_total"]

    if (
        running_session is not None
        or next_daily_task is not None
    ):
        remaining_after = progress["remaining_after"]
        if (
            remaining_after is not None
            and remaining_after > 0
        ):
            up_next_mode = "same_task"
            up_next_daily_task = session_daily_task

    return templates.TemplateResponse(
        request=request,
        name="timer.html",
        context={
            "selected_date": selected_date,
            "running_session": running_session,
            "break_until": active_break_until,
            "next_daily_task": next_daily_task,
            "session_number": session_number,
            "session_total": session_total,
            "remaining_after": remaining_after,
            "up_next_mode": up_next_mode,
            "up_next_daily_task": up_next_daily_task,
        },
    )


@router.post("/start")
def start_timer(
    duration_minutes: int | None = Form(None),
    db: Session = Depends(get_db),
):
    selected_date = today()

    try:
        start_work_session(
            db=db,
            target_date=selected_date,
            duration_minutes=duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return RedirectResponse(
        url="/timer/",
        status_code=303,
    )


@router.post("/end-early")
def end_timer_early(
    session_id: int = Form(...),
    db: Session = Depends(get_db),
):
    work_session = db.get(
        WorkSession,
        session_id,
    )

    if work_session is None:
        raise HTTPException(
            status_code=404,
            detail="Work session not found.",
        )

    try:
        end_work_session_early(
            db=db,
            work_session=work_session,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return RedirectResponse(
        url="/timer/",
        status_code=303,
    )


@router.post("/commit")
def commit_timer_session(
    session_id: int = Form(...),
    outcome: str = Form(...),
    interrupted: bool = Form(False),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
):
    work_session = db.get(
        WorkSession,
        session_id,
    )

    if work_session is None:
        raise HTTPException(
            status_code=404,
            detail="Work session not found.",
        )

    try:
        commit_work_session(
            db=db,
            work_session=work_session,
            outcome=outcome,
            interrupted=interrupted,
            note=note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    break_ends_at = (
        utc_now()
        + timedelta(
            minutes=get_settings().break_duration_minutes
        )
    )

    break_until = int(
        break_ends_at.timestamp()
    )

    return RedirectResponse(
        url=f"/timer/?break_until={break_until}",
        status_code=303,
    )
