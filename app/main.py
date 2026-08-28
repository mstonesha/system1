from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.http import (
    BrowserLoginRequired,
    browser_login_required_handler,
)
from app.config import get_settings
from app.logging import configure_logging, log_shutdown, log_startup
from app.routes.auth import router as auth_router
from app.routes.health import router as health_router
from app.routes.sessions import router as sessions_router
from app.routes.tasks import router as tasks_router
from app.routes.timer import router as timer_router
from app.routes.today import router as today_router
from app.routes.analysis import router as analysis_router
from app.routes.review import router as review_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log_startup()
    yield
    log_shutdown()


app = FastAPI(
    title=get_settings().app_name,
    lifespan=lifespan,
)

app.add_exception_handler(
    BrowserLoginRequired,
    browser_login_required_handler,
)

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(today_router)
app.include_router(sessions_router)
app.include_router(timer_router)
app.include_router(review_router)
app.include_router(analysis_router)
