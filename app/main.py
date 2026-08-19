from datetime import datetime

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task


app = FastAPI(title="System 1")

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)

templates = Jinja2Templates(
    directory="app/templates",
)


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------

def get_root_tasks(db: Session):
    return (
        db.query(Task)
        .filter(Task.parent_task_id.is_(None))
        .order_by(Task.sort_order, Task.created_at)
        .all()
    )


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


def has_active_descendants(task: Task) -> bool:
    for child in task.children:
        if child.status == "active":
            return True

        if has_active_descendants(child):
            return True

    return False


# ---------------------------------------------------------
# Main page
# ---------------------------------------------------------

@app.get("/")
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


# ---------------------------------------------------------
# JSON/API routes
# ---------------------------------------------------------

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
    task = Task(title=title.strip())

    db.add(task)
    db.commit()
    db.refresh(task)

    return task


# ---------------------------------------------------------
# Task-tree filtering
# ---------------------------------------------------------

@app.get("/task-tree")
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


# ---------------------------------------------------------
# Create tasks
# ---------------------------------------------------------

@app.post("/tasks/create")
def create_task_from_form(
    request: Request,
    title: str = Form(...),
    show_inactive: bool = Form(False),
    db: Session = Depends(get_db),
):
    task = Task(title=title.strip())

    db.add(task)
    db.commit()

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@app.post("/tasks/{parent_id}/subtasks")
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

    task = Task(
        title=title.strip(),
        parent_task_id=parent_id,
    )

    db.add(task)
    db.commit()

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


# ---------------------------------------------------------
# Task status
# ---------------------------------------------------------

@app.post("/tasks/{task_id}/complete")
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

    if has_active_descendants(task):
        return HTMLResponse(
            content="Cannot complete this task while it has active subtasks.",
            status_code=409,
        )

    task.status = "completed"
    task.completed_at = datetime.utcnow()

    db.commit()

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@app.post("/tasks/{task_id}/cancel")
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

    task.status = "cancelled"
    task.completed_at = None

    db.commit()

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


@app.post("/tasks/{task_id}/reopen")
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

    task.status = "active"
    task.completed_at = None

    db.commit()

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
    )


# ---------------------------------------------------------
# Editing
# ---------------------------------------------------------

@app.get("/tasks/{task_id}/edit")
def edit_task_form(
    task_id: int,
    request: Request,
    show_inactive: bool = False,
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

    return templates.TemplateResponse(
        request=request,
        name="partials/task_edit.html",
        context={
            "task": task,
            "show_inactive": show_inactive,
        },
    )


@app.post("/tasks/{task_id}/edit")
def update_task(
    task_id: int,
    request: Request,
    title: str = Form(...),
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

    task.title = title.strip()

    db.commit()
    db.refresh(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
            "show_inactive": False,
        },
    )


@app.get("/tasks/{task_id}/view")
def view_task(
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

    return templates.TemplateResponse(
        request=request,
        name="partials/task_item.html",
        context={
            "task": task,
            "show_inactive": False,
        },
    )