import os
from collections.abc import Callable, Generator
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.time import today

TEST_DATABASE_NAME = "system1_test"


def _test_database_url() -> str:
    url = os.environ.get("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Run the suite with: "
            "docker compose --profile test run --rm --build test"
        )

    database_name = make_url(url).database

    if database_name != TEST_DATABASE_NAME:
        raise RuntimeError(
            "Refusing to run tests against "
            f"database {database_name!r}. "
            f"Expected {TEST_DATABASE_NAME}."
        )

    return url


TEST_DATABASE_URL = _test_database_url()

from app.database import Base
from app.models import DailyTask, Task, WorkSession


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
                    "TRUNCATE work_sessions, daily_tasks, tasks "
                    "RESTART IDENTITY CASCADE"
                )
            )


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
