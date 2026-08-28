"""Application settings for Akrasia_Zero.

The product name is Akrasia_Zero. Some local database and Docker
identifiers still use the legacy system1 prefix; that is intentional
and does not indicate a second application.
"""

import os
from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy.engine import make_url


DEFAULT_APP_NAME = "Akrasia_Zero"
DEFAULT_DISPLAY_NAME = "Matt Stone"
DEFAULT_APP_TIMEZONE = "Europe/London"
DEFAULT_FOCUS_SESSION_MINUTES = 25
DEFAULT_BREAK_DURATION_MINUTES = 5
DEFAULT_LOG_LEVEL = "INFO"
ALLOWED_LOG_LEVELS = frozenset(
    {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
)
# Legacy local database name, not the product name.
TEST_DATABASE_NAME = "system1_test"
ANALYSIS_API_TOKEN_ENV = "AKRASIA_ANALYSIS_API_TOKEN"
PASSWORD_HASH_ENV = "AKRASIA_PASSWORD_HASH"
COOKIE_SECURE_ENV = "AKRASIA_COOKIE_SECURE"


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)

    if value is None:
        return None

    stripped = value.strip()

    if stripped == "":
        return None

    return stripped


def _env_str(name: str, default: str) -> str:
    value = _optional_env(name)

    if value is None:
        return default

    return value


def _env_int(name: str, default: int) -> int:
    value = _optional_env(name)

    if value is None:
        return default

    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = _optional_env(name)

    if value is None:
        return default

    lowered = value.lower()

    if lowered in {"1", "true", "yes", "on"}:
        return True

    if lowered in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"Invalid {name} {value!r}. "
        "Expected a boolean value."
    )


def _env_log_level() -> str:
    value = _env_str(
        "LOG_LEVEL",
        DEFAULT_LOG_LEVEL,
    ).upper()

    if value not in ALLOWED_LOG_LEVELS:
        allowed = ", ".join(
            sorted(ALLOWED_LOG_LEVELS)
        )
        raise ValueError(
            "Invalid LOG_LEVEL "
            f"{value!r}. Expected one of {allowed}."
        )

    return value


def _database_url_from_postgres_components() -> str | None:
    user = _optional_env("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    database = _optional_env("POSTGRES_DB")

    if not user or password is None or password == "" or not database:
        return None

    host = _optional_env("POSTGRES_HOST") or "db"
    port = _optional_env("POSTGRES_PORT") or "5432"

    return (
        "postgresql+psycopg://"
        f"{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )


def _resolve_database_url() -> str | None:
    explicit = _optional_env("DATABASE_URL")
    if explicit is not None:
        return explicit

    return _database_url_from_postgres_components()


@dataclass(frozen=True)
class Settings:
    app_name: str
    display_name: str
    timezone: str
    focus_session_minutes: int
    break_duration_minutes: int
    log_level: str
    database_url: str | None
    analysis_api_token: str | None = field(repr=False)
    password_hash: str | None = field(repr=False)
    cookie_secure: bool

    @property
    def break_duration_seconds(self) -> int:
        return self.break_duration_minutes * 60

    @property
    def focus_session_seconds(self) -> int:
        return self.focus_session_minutes * 60

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set."
            )

        return self.database_url


def get_settings() -> Settings:
    return Settings(
        app_name=_env_str(
            "APP_NAME",
            DEFAULT_APP_NAME,
        ),
        display_name=_env_str(
            "DISPLAY_NAME",
            DEFAULT_DISPLAY_NAME,
        ),
        timezone=_env_str(
            "APP_TIMEZONE",
            DEFAULT_APP_TIMEZONE,
        ),
        focus_session_minutes=_env_int(
            "FOCUS_SESSION_MINUTES",
            DEFAULT_FOCUS_SESSION_MINUTES,
        ),
        break_duration_minutes=_env_int(
            "BREAK_DURATION_MINUTES",
            DEFAULT_BREAK_DURATION_MINUTES,
        ),
        log_level=_env_log_level(),
        database_url=_resolve_database_url(),
        analysis_api_token=_optional_env(
            ANALYSIS_API_TOKEN_ENV
        ),
        password_hash=_optional_env(
            PASSWORD_HASH_ENV
        ),
        cookie_secure=_env_bool(
            COOKIE_SECURE_ENV,
            False,
        ),
    )


def require_test_database_url(
    url: str | None = None,
) -> str:
    if url is None:
        url = get_settings().database_url

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
