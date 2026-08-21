from datetime import date, timedelta

from fastapi import APIRouter, Form, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DailyTask, Task
from app.services.today import (
    add_task_to_day,
    get_carry_forward_candidates,
    get_daily_tasks_for_date,
    move_daily_task,
    remove_task_from_day,
    update_planned_sessions,
)
from app.time import today

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
    selected_date = target_date or today()

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
    selected_date = target_date or today()

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
    selected_date = target_date or today()

    previous_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    daily_tasks = get_daily_tasks_for_date(
        db=db,
        target_date=selected_date,
    )

    carry_forward_tasks = get_carry_forward_candidates(
        db=db,
        target_date=selected_date,
    )

    selected_task_ids = {
        daily_task.task_id
        for daily_task in daily_tasks
    }

    planned_by_task_id = {
        daily_task.task_id: daily_task
        for daily_task in daily_tasks
    }

    carry_forward_by_task_id = {
        daily_task.task_id: daily_task
        for daily_task in carry_forward_tasks
    }

    active_tasks = (
        db.query(Task)
        .filter(Task.status == "active")
        .all()
    )

    root_tasks = [
        task
        for task in active_tasks
        if task.parent_task_id is None
    ]

    root_tasks.sort(
        key=lambda task: (
            task.sort_order,
            task.created_at,
        )
    )

    def build_task_tree(task: Task) -> dict:
        active_children = [
            child
            for child in task.children
            if child.status == "active"
        ]

        active_children.sort(
            key=lambda child: (
                child.sort_order,
                child.created_at,
            )
        )

        return {
            "task": task,
            "children": [
                build_task_tree(child)
                for child in active_children
            ],
        }

    task_tree = [
        build_task_tree(task)
        for task in root_tasks
    ]

    return templates.TemplateResponse(
        request=request,
        name="today.html",
        context={
            "selected_date": selected_date,
            "previous_date": previous_date,
            "next_date": next_date,
            "daily_tasks": daily_tasks,
            "task_tree": task_tree,
            "selected_task_ids": selected_task_ids,
            "planned_by_task_id": planned_by_task_id,
            "carry_forward_by_task_id":
                carry_forward_by_task_id,
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

@router.post("/{daily_task_id}/commit-sessions")
def commit_planned_sessions(
    daily_task_id: int,
    planned_sessions: int = Form(...),
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

    try:
        update_planned_sessions(
            db=db,
            daily_task=daily_task,
            planned_sessions=planned_sessions,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return RedirectResponse(
        url=f"/today/page?target_date={target_date}",
        status_code=303,
    )

@router.post("/{daily_task_id}/move")
def move_daily_task_on_page(
    daily_task_id: int,
    direction: str = Form(...),
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

    selected_date = daily_task.date

    try:
        move_daily_task(
            db=db,
            daily_task=daily_task,
            direction=direction,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    return RedirectResponse(
        url=f"/today/page?target_date={selected_date}",
        status_code=303,
    )

@router.post("/carry-forward")
def carry_forward_task(
    task_id: int = Form(...),
    target_date: date = Form(...),
    planned_sessions: int | None = Form(None),
    db: Session = Depends(get_db),
):
    task = db.get(
        Task,
        task_id,
    )

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