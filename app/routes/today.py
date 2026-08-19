from datetime import date, timedelta

from fastapi import APIRouter, Form, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DailyTask, Task
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
    remove_task_from_day,
)

from fastapi.responses import RedirectResponse

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(
    directory="app/templates",
)


router = APIRouter(
    prefix="/today",
    tags=["today"],
)


@router.get("/")
def get_today(
    target_date: date | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or date.today()

    return get_daily_tasks_for_date(
        db=db,
        target_date=selected_date,
    )


@router.post("/add")
def add_to_today(
    task_id: int,
    target_date: date | None = None,
    planned_sessions: int | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or date.today()

    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    try:
        return add_task_to_day(
            db=db,
            task=task,
            target_date=selected_date,
            planned_sessions=planned_sessions,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )


@router.delete("/{daily_task_id}")
def remove_from_today(
    daily_task_id: int,
    db: Session = Depends(get_db),
):
    daily_task = db.get(
        DailyTask,
        daily_task_id,
    )

    if daily_task is None:
        raise HTTPException(
            status_code=404,
            detail="DailyTask not found.",
        )

    remove_task_from_day(
        db=db,
        daily_task=daily_task,
    )

    return {
        "deleted": True,
        "daily_task_id": daily_task_id,
    }

@router.get("/page")
def today_page(
    request: Request,
    target_date: date | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or date.today()

    previous_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=selected_date,
    )

    planned_task_ids = {
        daily_task.task_id
        for daily_task in daily_tasks
    }

    active_tasks = (
        db.query(Task)
        .filter(Task.status == "active")
        .order_by(Task.title)
        .all()
    )

    available_tasks = [
        task
        for task in active_tasks
        if task.id not in planned_task_ids
    ]

    return templates.TemplateResponse(
        request=request,
        name="today.html",
        context={
            "selected_date": selected_date,
            "previous_date": previous_date,
            "next_date": next_date,
            "daily_tasks": daily_tasks,
            "active_tasks": available_tasks,
        },
    )

@router.post("/add-from-page")
def add_to_today_from_page(
    task_id: int = Form(...),
    target_date: date = Form(...),
    planned_sessions: int | None = Form(None),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    try:
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
            planned_sessions=planned_sessions,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return RedirectResponse(
        url=f"/today/page?target_date={target_date}",
        status_code=303,
    )

@router.post("/{daily_task_id}/remove-from-page")
def remove_from_today_page(
    daily_task_id: int,
    target_date: date = Form(...),
    db: Session = Depends(get_db),
):
    daily_task = db.get(
        DailyTask,
        daily_task_id,
    )

    if daily_task is None:
        raise HTTPException(
            status_code=404,
            detail="DailyTask not found.",
        )

    remove_task_from_day(
        db=db,
        daily_task=daily_task,
    )

    return RedirectResponse(
        url=f"/today/page?target_date={target_date}",
        status_code=303,
    )