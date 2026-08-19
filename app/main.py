from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task
from datetime import datetime


app = FastAPI(title="System 1")


app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)


templates = Jinja2Templates(
    directory="app/templates",
)


@app.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
):
    tasks = (
        db.query(Task)
        .filter(Task.parent_task_id.is_(None))
        .order_by(Task.sort_order, Task.created_at)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={
            "tasks": tasks,
        },
    )


@app.get("/tasks")
def list_tasks(
    db: Session = Depends(get_db),
):
    return db.query(Task).all()


@app.post("/tasks")
def create_task(
    title: str,
    db: Session = Depends(get_db),
):
    task = Task(title=title)

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


@app.post("/tasks/create")
def create_task_from_form(
    request: Request,
    title: str = Form(...),
    db: Session = Depends(get_db),
):
    task = Task(title=title)

    db.add(task)
    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
        },
    )

@app.post("/tasks/{parent_id}/subtasks")
def create_subtask(
    parent_id: int,
    request: Request,
    title: str = Form(...),
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

    task = Task(
        title=title,
        parent_task_id=parent_id,
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
        },
    )

@app.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    if has_active_children(task):
        return HTMLResponse(
            content="Cannot complete this task while it has active subtasks.",
            status_code=409,
        )

    task.status = "completed"
    task.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
        },
    )


@app.post("/tasks/{task_id}/reopen")
def reopen_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    task.status = "active"
    task.completed_at = None

    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
        },
    )

def has_active_children(task: Task) -> bool:
    return any(
        child.status == "active"
        for child in task.children
    )

@app.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)

    if task is None:
        return HTMLResponse(
            content="Task not found",
            status_code=404,
        )

    task.status = "cancelled"
    task.completed_at = None

    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
        },
    )