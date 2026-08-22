from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import WorkSession
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
)


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

    session_number = None
    session_total = None
    session_daily_task = None

    if running_session is not None:
        session_daily_task = running_session.daily_task

    elif next_daily_task is not None:
        session_daily_task = next_daily_task

    if session_daily_task is not None:
        completed_session_count = (
            db.query(WorkSession)
            .filter(
                WorkSession.daily_task_id
                == session_daily_task.id,
                WorkSession.session_state == "completed",
            )
            .count()
        )

        session_number = completed_session_count + 1

        session_total = max(
            session_number,
            session_daily_task.planned_sessions or 1,
        )

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
