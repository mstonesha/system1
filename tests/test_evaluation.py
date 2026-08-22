import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.models import Task
from evaluation.database import (
    EVAL_DATABASE_NAME,
    require_eval_database_url,
    reset_eval_schema,
)
from evaluation.generate import main


def test_require_eval_database_url_accepts_eval_database():
    url = (
        "postgresql+psycopg://system1:system1@"
        f"db-eval:5432/{EVAL_DATABASE_NAME}"
    )

    assert require_eval_database_url(url) == url


def test_require_eval_database_url_rejects_development_database():
    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        require_eval_database_url(
            "postgresql+psycopg://system1:system1@db:5432/system1"
        )


def test_require_eval_database_url_rejects_test_database():
    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        require_eval_database_url(
            "postgresql+psycopg://system1:system1@"
            "db-test:5432/system1_test"
        )


def test_require_eval_database_url_requires_database_url(
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        RuntimeError,
        match="DATABASE_URL is not set",
    ):
        require_eval_database_url()


def test_cli_refuses_development_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://system1:system1@db:5432/system1",
    )

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        main(["temporal_patterns"])

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        main(["interruptions_dependencies"])

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        main(["task_age_abandonment"])


def test_reset_refuses_test_database_without_dropping(
    engine,
    db,
    make_task,
):
    make_task(title="must survive evaluation guard")

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        reset_eval_schema(engine)

    remaining = (
        db.query(Task)
        .filter(Task.title == "must survive evaluation guard")
        .one()
    )
    assert remaining.id is not None

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public'"
                )
            )
        }
    assert "tasks" in tables
    assert make_url(os.environ["DATABASE_URL"]).database == (
        "system1_test"
    )
