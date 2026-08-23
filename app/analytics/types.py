"""Typed results for deterministic analytics.

Session-outcome families use ISO 8601 weekday numbers
(Monday=1 through Sunday=7).

Task-age abandonment uses local calendar-day execution age
from first qualifying Today appearance to terminal date.

Planning families measure DailyTask capacity, completed-Task
effort estimation, and local ISO-week workload separately.

Morning-versus-afternoon windows measure committed session
outcomes in caller-chosen local date ranges. They do not
label periods as historical, recent, or changed.

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


@dataclass(frozen=True)
class DailyPlanningSummary:
    """DailyTask planned-versus-actual capacity in a local date range.

    ``planned_sessions IS NULL`` is counted in
    ``daily_tasks_without_explicit_plan`` and omitted from
    planned/actual/unused totals. ``execution_ratio`` is
    actual / planned, or None when planned is 0.
    Unused classes use current DailyTask.state.
    """

    from_date: date
    to_date: date
    timezone: str
    daily_tasks_with_explicit_plan: int
    daily_tasks_without_explicit_plan: int
    total_planned_sessions: int
    total_actual_sessions: int
    execution_ratio: float | None
    total_unused_planned_sessions: int
    unused_due_to_early_completion: int
    unused_while_unfinished: int
    unused_on_abandonment: int
    daily_tasks_actual_below_plan: int
    daily_tasks_actual_equal_plan: int
    daily_tasks_actual_above_plan: int


@dataclass(frozen=True)
class TaskEffortEstimation:
    """Completed-Task estimates versus lifetime committed sessions.

    Near estimate means actual == estimated (integer counts),
    not a ratio band. Means and median are unrounded.
    """

    from_date: date
    to_date: date
    timezone: str
    completed_tasks_with_estimate: int
    mean_estimated_sessions: float | None
    mean_actual_sessions: float | None
    mean_actual_to_estimated_ratio: float | None
    median_actual_to_estimated_ratio: float | None
    below_estimate_count: int
    near_estimate_count: int
    above_estimate_count: int


@dataclass(frozen=True)
class WeeklyWorkloadGroup:
    week_start_date: date
    daily_task_count: int
    daily_tasks_without_explicit_plan: int
    planned_sessions: int
    actual_sessions: int
    positive_sessions: int
    negative_sessions: int
    positive_rate: float | None
    negative_rate: float | None


@dataclass(frozen=True)
class WeeklyWorkloadAnalysis:
    from_date: date
    to_date: date
    timezone: str
    groups: tuple[WeeklyWorkloadGroup, ...]


@dataclass(frozen=True)
class MorningAfternoonWindow:
    """Committed session outcomes for morning vs afternoon.

    Morning is the existing ``morning`` daypart (09:00–11:59).
    Afternoon combines ``early_afternoon`` and ``late_afternoon``
    (12:00–17:59). Classification uses ``WorkSession.started_at``
    in ``APP_TIMEZONE``, not session end time.

    Rates and ``positive_rate_gap`` are None when the relevant
    side has no observations. ``positive_rate_gap`` is
    morning_positive_rate − afternoon_positive_rate.
    """

    from_date: date
    to_date: date
    timezone: str
    morning_session_count: int
    morning_positive_count: int
    morning_negative_count: int
    morning_positive_rate: float | None
    afternoon_session_count: int
    afternoon_positive_count: int
    afternoon_negative_count: int
    afternoon_positive_rate: float | None
    positive_rate_gap: float | None


@dataclass(frozen=True)
class MorningAfternoonWindowComparison:
    """Two explicit windows and their arithmetic rate differences."""

    current: MorningAfternoonWindow
    baseline: MorningAfternoonWindow
    morning_rate_change: float | None
    afternoon_rate_change: float | None
    gap_change: float | None


@dataclass(frozen=True)
class WeeklyMorningAfternoonGroup:
    week_start_date: date
    morning_session_count: int
    morning_positive_count: int
    morning_negative_count: int
    morning_positive_rate: float | None
    afternoon_session_count: int
    afternoon_positive_count: int
    afternoon_negative_count: int
    afternoon_positive_rate: float | None
    positive_rate_gap: float | None


@dataclass(frozen=True)
class WeeklyMorningAfternoonAnalysis:
    from_date: date
    to_date: date
    timezone: str
    groups: tuple[WeeklyMorningAfternoonGroup, ...]
