"""Shared helpers for evaluation-generator tests."""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.database import EVAL_DATABASE_NAME, reset_eval_schema


def server_url():
    return make_url(os.environ["DATABASE_URL"])


def eval_url():
    return server_url().set(database=EVAL_DATABASE_NAME)


def admin_engine() -> Engine:
    return create_engine(
        server_url().set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )


def drop_eval_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :name "
                "AND pid <> pg_backend_pid()"
            ),
            {"name": EVAL_DATABASE_NAME},
        )
        connection.execute(
            text(
                f'DROP DATABASE IF EXISTS "{EVAL_DATABASE_NAME}"'
            )
        )


def create_eval_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                f'CREATE DATABASE "{EVAL_DATABASE_NAME}"'
            )
        )


def snapshot(db: Session) -> tuple:
    tasks = [
        (
            task.title,
            task.status,
            task.parent_task_id,
            task.priority,
            task.estimated_sessions,
            (
                task.completed_at.isoformat()
                if task.completed_at
                else None
            ),
            task.created_at.isoformat(),
        )
        for task in db.query(Task).order_by(Task.id)
    ]
    daily_tasks = [
        (
            daily.task_id,
            daily.date.isoformat(),
            daily.planned_sessions,
            daily.state,
            daily.sort_order,
        )
        for daily in db.query(DailyTask).order_by(DailyTask.id)
    ]
    sessions = [
        (
            work.daily_task_id,
            work.started_at.isoformat(),
            work.ended_at.isoformat() if work.ended_at else None,
            work.outcome,
            work.interrupted,
            work.session_state,
            work.actual_duration_seconds,
        )
        for work in db.query(WorkSession).order_by(WorkSession.id)
    ]
    return (tasks, daily_tasks, sessions)


def generate_into(engine: Engine, generate_fn, seed: int):
    reset_eval_schema(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    with SessionLocal() as db:
        result = generate_fn(
            db,
            seed=seed,
            timezone_name=get_settings().timezone,
        )
        db.commit()
        snap = snapshot(db)
    return result, snap


def prepared_eval(generate_fn, seed: int):
    admin = admin_engine()
    engine = None
    try:
        drop_eval_database(admin)
        create_eval_database(admin)
        engine = create_engine(eval_url())
        first, first_snap = generate_into(engine, generate_fn, seed)
        second, second_snap = generate_into(engine, generate_fn, seed)
        SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )
        yield {
            "engine": engine,
            "result": second,
            "first_snap": first_snap,
            "second_snap": second_snap,
            "first_result": first,
            "SessionLocal": SessionLocal,
        }
    finally:
        if engine is not None:
            engine.dispose()
        drop_eval_database(admin)
        admin.dispose()
