"""Derived task-age helpers for evaluation datasets.

Age is not stored on Task. It is computed from DailyTask history.
"""

from datetime import date


AGE_BUCKETS = (
    ("0-2", 0, 2),
    ("3-7", 3, 7),
    ("8-14", 8, 14),
    ("15-30", 15, 30),
    ("31+", 31, None),
)


def calendar_age_days(start: date, end: date) -> int:
    return (end - start).days


def classify_age_days(days: int) -> str:
    if days < 0:
        raise ValueError("Age in days cannot be negative.")
    for name, low, high in AGE_BUCKETS:
        if high is None:
            if days >= low:
                return name
        elif low <= days <= high:
            return name
    raise ValueError(f"No age bucket for {days} days.")


def bucket_bounds(name: str) -> tuple[int, int | None]:
    for bucket_name, low, high in AGE_BUCKETS:
        if bucket_name == name:
            return low, high
    raise KeyError(name)
