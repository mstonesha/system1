from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task


app = FastAPI(title="System 1")


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>System 1</title>
        </head>
        <body>
            <h1>System 1</h1>
            <p>The application is running.</p>
        </body>
    </html>
    """


@app.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
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