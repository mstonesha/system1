import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
DEFAULT_APP_TIMEZONE = "Europe/London"


def app_timezone() -> ZoneInfo:
    return ZoneInfo(
        os.environ.get(
            "APP_TIMEZONE",
            DEFAULT_APP_TIMEZONE,
        )
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return datetime.now(app_timezone()).date()


def local_day_bounds_utc(
    target_date: date,
) -> tuple[datetime, datetime]:
    """Return the UTC [start, end) covering a local calendar date."""
    zone = app_timezone()
    start_local = datetime.combine(
        target_date,
        time.min,
        tzinfo=zone,
    )
    end_local = datetime.combine(
        target_date + timedelta(days=1),
        time.min,
        tzinfo=zone,
    )
    return (
        start_local.astimezone(UTC),
        end_local.astimezone(UTC),
    )
