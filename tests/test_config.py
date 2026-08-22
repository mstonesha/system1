import pytest

from app.config import (
    DEFAULT_APP_NAME,
    DEFAULT_APP_TIMEZONE,
    DEFAULT_BREAK_DURATION_MINUTES,
    DEFAULT_DISPLAY_NAME,
    DEFAULT_FOCUS_SESSION_MINUTES,
    DEFAULT_LOG_LEVEL,
    TEST_DATABASE_NAME,
    get_settings,
    require_test_database_url,
)


def test_default_settings_resolve_correctly(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("DISPLAY_NAME", raising=False)
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    monkeypatch.delenv("FOCUS_SESSION_MINUTES", raising=False)
    monkeypatch.delenv("BREAK_DURATION_MINUTES", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = get_settings()

    assert settings.app_name == DEFAULT_APP_NAME
    assert settings.app_name == "Akrasia_Zero"
    assert settings.display_name == DEFAULT_DISPLAY_NAME
    assert settings.display_name == "Matt Stone"
    assert settings.timezone == DEFAULT_APP_TIMEZONE
    assert settings.timezone == "Europe/London"
    assert (
        settings.focus_session_minutes
        == DEFAULT_FOCUS_SESSION_MINUTES
    )
    assert settings.focus_session_minutes == 25
    assert (
        settings.break_duration_minutes
        == DEFAULT_BREAK_DURATION_MINUTES
    )
    assert settings.break_duration_minutes == 5
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.log_level == "INFO"
    assert settings.focus_session_seconds == 25 * 60
    assert settings.break_duration_seconds == 5 * 60


def test_environment_overrides_apply_to_settings(monkeypatch):
    monkeypatch.setenv("APP_NAME", "FocusLab")
    monkeypatch.setenv("DISPLAY_NAME", "Ada Lovelace")
    monkeypatch.setenv("APP_TIMEZONE", "America/New_York")
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    monkeypatch.setenv("BREAK_DURATION_MINUTES", "7")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:pass@localhost:5432/custom",
    )

    settings = get_settings()

    assert settings.app_name == "FocusLab"
    assert settings.display_name == "Ada Lovelace"
    assert settings.timezone == "America/New_York"
    assert settings.focus_session_minutes == 40
    assert settings.break_duration_minutes == 7
    assert settings.log_level == "WARNING"
    assert (
        settings.database_url
        == "postgresql+psycopg://user:pass@localhost:5432/custom"
    )


def test_database_url_has_no_silent_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = get_settings()

    assert settings.database_url is None

    with pytest.raises(
        RuntimeError,
        match="DATABASE_URL is not set",
    ):
        settings.require_database_url()


def test_blank_environment_values_use_defaults(monkeypatch):
    monkeypatch.setenv("APP_NAME", "  ")
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "")

    settings = get_settings()

    assert settings.app_name == DEFAULT_APP_NAME
    assert (
        settings.focus_session_minutes
        == DEFAULT_FOCUS_SESSION_MINUTES
    )


def test_require_test_database_url_accepts_test_database():
    url = (
        "postgresql+psycopg://system1:system1@"
        f"db-test:5432/{TEST_DATABASE_NAME}"
    )

    assert require_test_database_url(url) == url


def test_require_test_database_url_rejects_other_databases():
    with pytest.raises(
        RuntimeError,
        match="Refusing to run tests against",
    ):
        require_test_database_url(
            "postgresql+psycopg://system1:system1@db:5432/system1"
        )


def test_invalid_log_level_rejected(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

    with pytest.raises(
        ValueError,
        match="Invalid LOG_LEVEL",
    ):
        get_settings()


def test_require_test_database_url_requires_database_url(
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        RuntimeError,
        match="DATABASE_URL is not set",
    ):
        require_test_database_url()
