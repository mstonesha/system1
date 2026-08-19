from fastapi import Depends, FastAPI, Form, Request
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