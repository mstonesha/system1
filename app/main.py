from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.tasks import router as tasks_router


app = FastAPI(title="System 1")

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)

app.include_router(tasks_router)