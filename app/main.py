from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.sessions import router as sessions_router
from app.routes.tasks import router as tasks_router
from app.routes.timer import router as timer_router
from app.routes.today import router as today_router
from app.routes.review import router as review_router


app = FastAPI(title="System 1")

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)

app.include_router(tasks_router)
app.include_router(today_router)
app.include_router(sessions_router)
app.include_router(timer_router)
app.include_router(review_router)