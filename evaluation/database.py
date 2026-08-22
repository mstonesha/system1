"""Evaluation-database guards and schema reset.

The generator may only connect to system1_agent_eval. It must never
reset development (system1) or automated-test (system1_test) data.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from app.config import get_settings


EVAL_DATABASE_NAME = "system1_agent_eval"


def require_eval_database_url(
    url: str | None = None,
) -> str:
    if url is None:
        url = get_settings().database_url

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Run generation with: "
            "docker compose --profile eval run --rm eval"
        )

    database_name = make_url(url).database

    if database_name != EVAL_DATABASE_NAME:
        raise RuntimeError(
            "Refusing to generate evaluation data against "
            f"database {database_name!r}. "
            f"Expected {EVAL_DATABASE_NAME}."
        )

    return url


def create_eval_engine(url: str | None = None) -> Engine:
    return create_engine(require_eval_database_url(url))


def reset_eval_schema(engine: Engine) -> None:
    """Drop and recreate public schema, then apply Alembic head.

    The engine must already point at system1_agent_eval. This is
    checked before any destructive statement runs.
    """
    if engine.url.database != EVAL_DATABASE_NAME:
        raise RuntimeError(
            "Refusing to generate evaluation data against "
            f"database {engine.url.database!r}. "
            f"Expected {EVAL_DATABASE_NAME}."
        )

    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(
            text(
                "GRANT ALL ON SCHEMA public TO CURRENT_USER"
            )
        )
        connection.execute(
            text("GRANT ALL ON SCHEMA public TO public")
        )

    _upgrade_to_head(engine)


def _upgrade_to_head(engine: Engine) -> None:
    config = Config("alembic.ini")

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
