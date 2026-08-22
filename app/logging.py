"""Stdlib logging for Akrasia_Zero.

Logs go to stdout/stderr only. Do not log task titles, descriptions,
session notes, request bodies, passwords, or database URLs.
"""

import logging
import sys

from sqlalchemy.engine import make_url

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)

    root = logging.getLogger()

    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            stream=sys.stderr,
        )


def describe_database(
    database_url: str | None,
) -> str:
    if not database_url:
        return "database=unset"

    url = make_url(database_url)
    host = url.host or "unknown"
    database = url.database or "unknown"

    if url.port is not None:
        host = f"{host}:{url.port}"

    return f"database={database} host={host}"


def log_startup() -> None:
    settings = get_settings()
    logger = logging.getLogger("app")

    logger.info(
        "Starting %s timezone=%s focus_minutes=%s "
        "break_minutes=%s log_level=%s %s",
        settings.app_name,
        settings.timezone,
        settings.focus_session_minutes,
        settings.break_duration_minutes,
        settings.log_level,
        describe_database(settings.database_url),
    )


def log_shutdown() -> None:
    settings = get_settings()
    logger = logging.getLogger("app")

    logger.info(
        "Shutting down %s",
        settings.app_name,
    )
