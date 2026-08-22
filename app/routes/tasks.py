from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task
from app.services.tasks import (
    cancel_task as cancel_task_service,
    complete_task as complete_task_service,
    create_task as create_task_service,
    get_root_tasks,
    reopen_task as reopen_task_service,
    update_task_title,
)
from app.templating import templates


router = APIRouter()


def render_task_tree(
    request: Request,
    db: Session,
    show_inactive: bool = False,
):
    tasks = get_root_tasks(db)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_tree.html",
        context={
            "tasks": tasks,
            "show_inactive": show_inactive,
        },
    )


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
):
    tasks = get_root_tasks(db)

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={
            "tasks": tasks,
            "show_inactive": False,
        },
    )


@router.get("/tasks")
def list_tasks(
    db: Session = Depends(get_db),
):
    return db.query(Task).all()


@router.post("/tasks")
def create_task_api(
    title: str,
    db: Session = Depends(get_db),
):
    try:
        return create_task_service(
            db=db,
            title=title,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )


@router.get("/task-tree")
def task_tree(
    request: Request,
    show_inactive: bool = False,
    db: Session = Depends(get_db),
):
    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@router.post("/tasks/create")
def create_task_from_form(
    request: Request,
    title: str = Form(...),
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        create_task_service(
            db=db,
            title=title,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=str(exc),
            status_code=409,
        )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@router.post("/tasks/{parent_id}/subtasks")
def create_subtask(
    parent_id: int,
    request: Request,
    title: str = Form(...),
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    parent = db.get(Task, parent_id)

    if parent is None:
        return HTMLResponse(
            content="Parent task not found",
            status_code=404,
        )

    if parent.status != "active":
        return HTMLResponse(
            content="Subtasks can only be added to active tasks.",
            status_code=409,
        )

    try:
        create_task_service(
            db=db,
            title=title,
            parent_task_id=parent_id,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=str(exc),
            status_code=409,
        )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    try:
        complete_task_service(
            db=db,
            task=task,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=str(exc),
            status_code=409,
        )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    cancel_task_service(
        db=db,
        task=task,
    )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@router.post("/tasks/{task_id}/reopen")
def reopen_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    reopen_task_service(
        db=db,
        task=task,
    )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )

@router.post("/tasks/{task_id}/edit")
def edit_task(
    task_id: int,
    request: Request,
    title: str = Form(...),
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    if task.status != "active":
        return HTMLResponse(
            content="Only active tasks can be edited.",
            status_code=409,
        )

    try:
        update_task_title(
            db=db,
            task=task,
            title=title,
        )
    except ValueError as exc:
        return HTMLResponse(
            content=str(exc),
            status_code=409,
        )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )