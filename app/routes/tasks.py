from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.http import require_browser_session
from app.database import get_db
from app.models import Task
from app.services.tasks import (
    OLDEST_TASK_SORT,
    cancel_stale_tasks as cancel_stale_tasks_service,
    cancel_task as cancel_task_service,
    complete_task as complete_task_service,
    create_task as create_task_service,
    get_root_tasks,
    normalize_task_sort,
    reopen_task as reopen_task_service,
    sort_task_siblings,
    update_task,
)
from app.templating import templates
from app.time import utc_now


router = APIRouter(
    dependencies=[Depends(require_browser_session)],
)


def _resolve_task_sort(
    sort: str | None,
) -> tuple[str, HTMLResponse | None]:
    try:
        return normalize_task_sort(sort), None
    except ValueError as exc:
        return (
            OLDEST_TASK_SORT,
            HTMLResponse(
                content=str(exc),
                status_code=409,
            ),
        )


def render_task_tree(
    request: Request,
    db: Session,
    show_inactive: bool = False,
    sort: str = OLDEST_TASK_SORT,
):
    tasks = sort_task_siblings(
        get_root_tasks(db),
        sort,
    )

    return templates.TemplateResponse(
        request=request,
        name="partials/task_tree.html",
        context={
            "tasks": tasks,
            "show_inactive": show_inactive,
            "task_sort": sort,
        },
    )


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
):
    tasks = sort_task_siblings(
        get_root_tasks(db),
        OLDEST_TASK_SORT,
    )

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={
            "tasks": tasks,
            "show_inactive": False,
            "task_sort": OLDEST_TASK_SORT,
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
    sort: str | None = None,
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
        sort=task_sort,
    )


@router.post("/tasks/create")
def create_task_from_form(
    request: Request,
    title: str = Form(...),
    area: str | None = Form(None),
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

    try:
        create_task_service(
            db=db,
            title=title,
            area=area,
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
        sort=task_sort,
    )


@router.post("/tasks/{parent_id}/subtasks")
def create_subtask(
    parent_id: int,
    request: Request,
    title: str = Form(...),
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

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
        sort=task_sort,
    )


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

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
        sort=task_sort,
    )


@router.post("/tasks/cancel-stale")
def cancel_stale_tasks(
    request: Request,
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

    cancel_stale_tasks_service(
        db=db,
        as_of=utc_now(),
    )

    return render_task_tree(
        request=request,
        db=db,
        show_inactive=show_inactive,
        sort=task_sort,
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

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
        sort=task_sort,
    )


@router.post("/tasks/{task_id}/reopen")
def reopen_task(
    task_id: int,
    request: Request,
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

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
        sort=task_sort,
    )

@router.post("/tasks/{task_id}/edit")
def edit_task(
    task_id: int,
    request: Request,
    title: str = Form(...),
    area: str | None = Form(None),
    show_inactive: bool = Form(False),
    sort: str | None = Form(None),
    db: Session = Depends(get_db),
):
    task_sort, error = _resolve_task_sort(sort)
    if error is not None:
        return error

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
        update_task(
            db=db,
            task=task,
            title=title,
            area=area,
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
        sort=task_sort,
    )