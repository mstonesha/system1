"""Local-time helpers for evaluation datasets."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

DAY_PART_SPECS = (
    ("early_morning", time(6, 0), time(9, 0), 0.05),
    ("morning", time(9, 0), time(12, 0), 0.35),
    ("early_afternoon", time(12, 0), time(15, 0), 0.30),
    ("late_afternoon", time(15, 0), time(18, 0), 0.25),
    ("evening", time(18, 0), time(22, 0), 0.05),
)

DAY_PART_WEIGHTS = {
    name: weight
    for name, _start, _end, weight in DAY_PART_SPECS
}

DAY_PART_BOUNDS = {
    name: (start, end)
    for name, start, end, _weight in DAY_PART_SPECS
}

DAY_PARTS = tuple(DAY_PART_WEIGHTS)


def working_days(start: date, weeks: int) -> list[date]:
    """Return Monday–Friday dates covering `weeks` calendar weeks."""
    if start.weekday() != 0:
        raise ValueError(
            "Evaluation scenarios must start on a Monday."
        )

    days: list[date] = []
    cursor = start
    end = start + timedelta(weeks=weeks)

    while cursor < end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)

    return days


def classify_day_part(local_dt: datetime) -> str:
    local_time = local_dt.timetz().replace(tzinfo=None)

    if local_time < time(6, 0):
        return "evening"
    if local_time < time(9, 0):
        return "early_morning"
    if local_time < time(12, 0):
        return "morning"
    if local_time < time(15, 0):
        return "early_afternoon"
    if local_time < time(18, 0):
        return "late_afternoon"
    return "evening"


def local_to_utc(local_dt: datetime) -> datetime:
    if local_dt.tzinfo is None:
        raise ValueError("Expected a timezone-aware local datetime.")
    return local_dt.astimezone(UTC)


def combine_local(
    target_date: date,
    local_time: time,
    zone: ZoneInfo,
) -> datetime:
    return datetime.combine(
        target_date,
        local_time,
        tzinfo=zone,
    )
