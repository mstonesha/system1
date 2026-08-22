from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.sessions import (
    get_running_session,
    start_work_session,
)
from app.time import today


router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
)


@router.get("/running")
def running_session(
    db: Session = Depends(get_db),
):
    return get_running_session(db)


@router.post("/start")
def start_session(
    target_date: date | None = None,
    duration_minutes: int | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or today()

    try:
        return start_work_session(
            db=db,
            target_date=selected_date,
            duration_minutes=duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )