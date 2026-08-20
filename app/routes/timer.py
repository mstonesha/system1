from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WorkSession
from app.services.sessions import (
    commit_work_session,
    get_next_daily_task,
    get_running_session,
    start_work_session,
)


router = APIRouter(
    prefix="/timer",
    tags=["timer"],
)

templates = Jinja2Templates(
    directory="app/templates",
)


@router.get("/")
def timer_page(
    request: Request,
    db: Session = Depends(get_db),
):
    selected_date = date.today()

    running_session = get_running_session(db)

    next_daily_task = None

    if running_session is None:
        next_daily_task = get_next_daily_task(
            db=db,
            target_date=selected_date,
        )

    return templates.TemplateResponse(
        request=request,
        name="timer.html",
        context={
            "selected_date": selected_date,
            "running_session": running_session,
            "next_daily_task": next_daily_task,
        },
    )


@router.post("/start")
def start_timer(
    duration_minutes: int = Form(25),
    db: Session = Depends(get_db),
):
    selected_date = date.today()

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

    return RedirectResponse(
        url="/timer/",
        status_code=303,
    )