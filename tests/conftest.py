from collections.abc import Callable, Generator
from datetime import date, datetime

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.http import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.auth.sessions import issue_session
from app.config import (
    ANALYSIS_API_TOKEN_ENV,
    COOKIE_SECURE_ENV,
    PASSWORD_HASH_ENV,
    require_test_database_url,
)
from app.time import today

TEST_DATABASE_URL = require_test_database_url()

from app.database import Base, get_db
from app.main import app
from app.models import DailyTask, Task, WorkSession


TEST_OPERATOR_PASSWORD = (
    "test-operator-password-not-a-secret"
)
TEST_ANALYSIS_API_TOKEN = (
    "test-analysis-api-token-not-a-real-secret"
)
STATE_CHANGING_METHODS = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)


class _SuppressRehashHasher:
    """Keep production hasher params; skip expected test rehash flags."""

    def __init__(self, inner):
        self._inner = inner

    def check_needs_rehash(self, encoded):
        return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


class CSRFAwareTestClient(TestClient):
    def __init__(self, *args, **kwargs):
        self.csrf_token = kwargs.pop("csrf_token", "")
        self.raw_session_token = kwargs.pop(
            "raw_session_token",
            "",
        )
        self.inject_csrf = kwargs.pop("inject_csrf", True)
        super().__init__(*args, **kwargs)

    def request(self, method, url, **kwargs):
        if (
            self.inject_csrf
            and self.csrf_token
            and str(method).upper() in STATE_CHANGING_METHODS
        ):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(
                "X-CSRF-Token",
                self.csrf_token,
            )
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture(scope="session")
def operator_password_hash() -> str:
    return PasswordHasher(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
    ).hash(TEST_OPERATOR_PASSWORD)


@pytest.fixture(autouse=True)
def configure_operator_auth(
    monkeypatch,
    operator_password_hash,
):
    monkeypatch.setenv(
        PASSWORD_HASH_ENV,
        operator_password_hash,
    )
    monkeypatch.setenv(COOKIE_SECURE_ENV, "false")
    monkeypatch.setenv(
        ANALYSIS_API_TOKEN_ENV,
        TEST_ANALYSIS_API_TOKEN,
    )
    # Low-cost test hashes would otherwise warn on every login.
    from app.auth import password as password_mod

    monkeypatch.setattr(
        password_mod,
        "_hasher",
        _SuppressRehashHasher(password_mod._hasher),
    )


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    test_engine = create_engine(TEST_DATABASE_URL)

    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)

    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()

        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE browser_sessions, work_sessions, "
                    "daily_tasks, tasks "
                    "RESTART IDENTITY CASCADE"
                )
            )


def _override_db(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def client(db):
    _override_db(db)
    issued = issue_session(db, user_agent="pytest")

    with CSRFAwareTestClient(
        app,
        csrf_token=issued.raw_csrf,
        raw_session_token=issued.raw_token,
        inject_csrf=True,
    ) as test_client:
        test_client.cookies.set(
            SESSION_COOKIE_NAME,
            issued.raw_token,
        )
        test_client.cookies.set(
            CSRF_COOKIE_NAME,
            issued.raw_csrf,
        )
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db):
    _override_db(db)

    with CSRFAwareTestClient(
        app,
        inject_csrf=False,
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def make_task(db: Session) -> Callable[..., Task]:
    def _make_task(
        title: str = "Test task",
        *,
        parent: Task | None = None,
        status: str = "active",
        **kwargs,
    ) -> Task:
        task = Task(
            title=title,
            parent_task_id=(
                parent.id if parent is not None else None
            ),
            status=status,
            **kwargs,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    return _make_task


@pytest.fixture
def make_daily_task(db: Session) -> Callable[..., DailyTask]:
    def _make_daily_task(
        task: Task,
        *,
        target_date: date | None = None,
        planned_sessions: int | None = 1,
        state: str = "planned",
        sort_order: int = 0,
    ) -> DailyTask:
        daily_task = DailyTask(
            task_id=task.id,
            date=target_date or today(),
            planned_sessions=planned_sessions,
            state=state,
            sort_order=sort_order,
        )
        db.add(daily_task)
        db.commit()
        db.refresh(daily_task)
        return daily_task

    return _make_daily_task


@pytest.fixture
def make_work_session(db: Session) -> Callable[..., WorkSession]:
    def _make_work_session(
        daily_task: DailyTask,
        *,
        session_state: str = "running",
        planned_duration_seconds: int = 1500,
        started_at: datetime | None = None,
        **kwargs,
    ) -> WorkSession:
        work_session = WorkSession(
            daily_task_id=daily_task.id,
            session_state=session_state,
            planned_duration_seconds=planned_duration_seconds,
            **kwargs,
        )

        if started_at is not None:
            work_session.started_at = started_at

        db.add(work_session)
        db.commit()
        db.refresh(work_session)
        return work_session

    return _make_work_session
