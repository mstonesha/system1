from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.time import app_timezone, local_day_bounds_utc

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def test_gmt_day_bounds_match_utc():
    start, end = local_day_bounds_utc(date(2026, 1, 15))

    assert start == datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 1, 16, 0, 0, tzinfo=UTC)


def test_bst_day_bounds_are_shifted_from_utc():
    start, end = local_day_bounds_utc(date(2026, 7, 15))

    assert start == datetime(2026, 7, 14, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 15, 23, 0, tzinfo=UTC)


def test_local_midnight_boundaries_during_bst():
    start, end = local_day_bounds_utc(date(2026, 7, 15))

    last_moment_previous_day = datetime(
        2026, 7, 14, 23, 59, tzinfo=LONDON
    ).astimezone(UTC)
    first_moment_of_day = datetime(
        2026, 7, 15, 0, 0, tzinfo=LONDON
    ).astimezone(UTC)

    assert last_moment_previous_day < start
    assert start == first_moment_of_day
    assert start < end


def test_spring_forward_local_day_is_twenty_three_hours():
    start, end = local_day_bounds_utc(date(2026, 3, 29))

    assert start == datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 29, 23, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=23)


def test_fall_back_local_day_is_twenty_five_hours():
    start, end = local_day_bounds_utc(date(2026, 10, 25))

    assert start == datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    assert end - start == timedelta(hours=25)


def test_app_timezone_defaults_to_europe_london(monkeypatch):
    monkeypatch.delenv("APP_TIMEZONE", raising=False)

    assert str(app_timezone()) == "Europe/London"


def test_app_timezone_uses_environment_override(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "America/New_York")

    assert str(app_timezone()) == "America/New_York"


def test_timezone_helpers_use_configured_settings(monkeypatch):
    monkeypatch.setattr(
        "app.time.get_settings",
        lambda: SimpleNamespace(timezone="America/New_York"),
    )

    assert str(app_timezone()) == "America/New_York"

    start, end = local_day_bounds_utc(date(2026, 1, 15))

    assert start == datetime(
        2026, 1, 15, 5, 0, tzinfo=UTC
    )
    assert end == datetime(
        2026, 1, 16, 5, 0, tzinfo=UTC
    )
