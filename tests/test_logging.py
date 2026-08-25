import logging

import pytest

import app.services.sessions as sessions_mod
from app.config import ANALYSIS_API_TOKEN_ENV, get_settings
from app.logging import describe_database, log_startup
from app.services.sessions import start_work_session
from app.services.today import add_task_to_day
from app.time import today


SECRET_DATABASE_URL = (
    "postgresql+psycopg://alice:s3cretpass@db.example:5432/appdb"
)
SECRET_ANALYSIS_API_TOKEN = (
    "test-logging-analysis-api-token-not-a-real-secret"
)


def test_describe_database_omits_credentials():
    summary = describe_database(SECRET_DATABASE_URL)

    assert "appdb" in summary
    assert "db.example" in summary
    assert "s3cretpass" not in summary
    assert "alice" not in summary
    assert "postgresql+psycopg://" not in summary
    assert "@" not in summary


def test_describe_database_unset():
    assert describe_database(None) == "database=unset"


def test_startup_log_omits_database_credentials(
    monkeypatch,
    caplog,
):
    monkeypatch.setenv("DATABASE_URL", SECRET_DATABASE_URL)
    monkeypatch.setenv(
        ANALYSIS_API_TOKEN_ENV,
        SECRET_ANALYSIS_API_TOKEN,
    )
    caplog.set_level(logging.INFO, logger="app")
    # Alembic fileConfig during eval schema reset disables existing
    # loggers in this process. Re-enable so startup logging is visible.
    logging.getLogger("app").disabled = False

    log_startup()

    text = caplog.text
    settings = get_settings()

    assert settings.app_name in text
    assert settings.timezone in text
    assert "s3cretpass" not in text
    assert "alice" not in text
    assert "postgresql+psycopg://" not in text
    assert "appdb" in text
    assert SECRET_ANALYSIS_API_TOKEN not in text
    assert "AKRASIA_ANALYSIS_API_TOKEN" not in text


def test_expected_conflict_is_not_logged_as_error(
    client,
    caplog,
):
    caplog.set_level(logging.WARNING)

    response = client.post(
        "/timer/start",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert not any(
        record.levelno >= logging.ERROR
        and record.name.startswith("app")
        for record in caplog.records
    )


def test_concurrent_session_start_logs_warning_not_error(
    db,
    make_task,
    monkeypatch,
):
    warnings = []

    def capture_warning(message, *args, **kwargs):
        warnings.append(message % args if args else message)

    monkeypatch.setattr(
        sessions_mod.logger,
        "warning",
        capture_warning,
    )

    target_date = today()
    task = make_task("Focus")
    add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    first = start_work_session(
        db=db,
        target_date=target_date,
    )

    monkeypatch.setattr(
        "app.services.sessions.get_running_session",
        lambda db: None,
    )

    with pytest.raises(
        ValueError,
        match="already running",
    ):
        start_work_session(
            db=db,
            target_date=target_date,
        )

    assert warnings
    message = warnings[0]
    assert "unique constraint" in message
    assert str(first.daily_task_id) in message
    assert "Focus" not in message
