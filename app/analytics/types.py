"""Typed results for deterministic analytics.

Session-outcome families use ISO 8601 weekday numbers
(Monday=1 through Sunday=7).

Task-age abandonment uses local calendar-day execution age
from first qualifying Today appearance to terminal date.

Rates are unrounded floating-point proportions in [0, 1],
not percentages. Rounding belongs at presentation
boundaries, not in this layer.
"""

from dataclasses import dataclass
from datetime import date


COMMITTED_OUTCOMES = (
    "progress",
    "complete",
    "stuck",
    "paused",
    "abandoned",
)
POSITIVE_OUTCOMES = frozenset({"progress", "complete"})
NEGATIVE_OUTCOMES = frozenset(
    {"stuck", "paused", "abandoned"}
)

ISO_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def iso_weekday_name(weekday_number: int) -> str:
    """Return the English weekday name for an ISO weekday.

    ``weekday_number`` is Monday=1 through Sunday=7.
    """
    if not 1 <= weekday_number <= 7:
        raise ValueError(
            "weekday_number must be ISO 8601 "
            f"Monday=1 through Sunday=7; got {weekday_number}."
        )
    return ISO_WEEKDAY_NAMES[weekday_number - 1]


@dataclass(frozen=True)
class WeekdayOutcomeGroup:
    weekday: str
    weekday_number: int
    session_count: int
    progress_count: int
    complete_count: int
    stuck_count: int
    paused_count: int
    abandoned_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    negative_rate: float


@dataclass(frozen=True)
class DaypartOutcomeGroup:
    daypart: str
    session_count: int
    progress_count: int
    complete_count: int
    stuck_count: int
    paused_count: int
    abandoned_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    negative_rate: float


@dataclass(frozen=True)
class WeekdayDaypartOutcomeGroup:
    weekday: str
    weekday_number: int
    daypart: str
    session_count: int
    progress_count: int
    complete_count: int
    stuck_count: int
    paused_count: int
    abandoned_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    negative_rate: float


@dataclass(frozen=True)
class InterruptionOutcomeGroup:
    interrupted: bool
    session_count: int
    progress_count: int
    complete_count: int
    stuck_count: int
    paused_count: int
    abandoned_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    negative_rate: float


@dataclass(frozen=True)
class OutcomeAnalysis[GroupT]:
    from_date: date
    to_date: date
    timezone: str
    total_sessions: int
    groups: tuple[GroupT, ...]


@dataclass(frozen=True)
class TaskAgeBucket:
    age_bucket: str
    min_days: int
    max_days: int | None
    terminal_task_count: int
    completed_count: int
    abandoned_count: int
    abandonment_rate: float
    completion_rate: float


@dataclass(frozen=True)
class TaskAgeAnalysis:
    from_date: date
    to_date: date
    timezone: str
    total_terminal_tasks: int
    groups: tuple[TaskAgeBucket, ...]


@dataclass(frozen=True)
class TerminalTaskAge:
    task_id: int
    first_today_date: date
    terminal_date: date
    execution_age_days: int
    terminal_outcome: str
