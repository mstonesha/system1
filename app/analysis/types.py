"""Typed production analytical packages.

These objects are the approved answers the rest of the
system may request. They assemble deterministic analytics
without interpreting them.

Dates on ``AnalysisPeriod`` are inclusive local calendar
dates in ``APP_TIMEZONE``. This layer does not cap period
length; callers choose the range.

Row types are the frozen production dataclasses from
``app.analytics``. Packages add period metadata and a
stable composition, not new calculations.
"""

from dataclasses import dataclass
from datetime import date

from app.analytics.types import (
    DailyPlanningSummary,
    DaypartOutcomeGroup,
    InterruptionOutcomeGroup,
    MorningAfternoonWindow,
    MorningAfternoonWindowComparison,
    StuckTaskObservation,
    TaskAgeBucket,
    TaskEffortEstimation,
    TerminalTaskAge,
    WeekdayDaypartOutcomeGroup,
    WeekdayOutcomeGroup,
    WeeklyMorningAfternoonAnalysis,
    WeeklyWorkloadAnalysis,
)


@dataclass(frozen=True)
class AnalysisPeriod:
    from_date: date
    to_date: date
    timezone: str


@dataclass(frozen=True)
class TemporalSummary:
    period: AnalysisPeriod
    total_sessions: int
    by_weekday: tuple[WeekdayOutcomeGroup, ...]
    by_daypart: tuple[DaypartOutcomeGroup, ...]
    by_weekday_daypart: tuple[WeekdayDaypartOutcomeGroup, ...]
    morning_afternoon: MorningAfternoonWindow


@dataclass(frozen=True)
class InterruptionSummary:
    period: AnalysisPeriod
    total_sessions: int
    groups: tuple[InterruptionOutcomeGroup, ...]


@dataclass(frozen=True)
class TaskAgeSummary:
    period: AnalysisPeriod
    total_terminal_tasks: int
    buckets: tuple[TaskAgeBucket, ...]


@dataclass(frozen=True)
class TerminalTaskAgeDrilldown:
    period: AnalysisPeriod
    limit: int
    returned_task_count: int
    tasks: tuple[TerminalTaskAge, ...]


@dataclass(frozen=True)
class PlanningSummary:
    period: AnalysisPeriod
    daily_planning: DailyPlanningSummary
    task_effort: TaskEffortEstimation
    weekly_workload: WeeklyWorkloadAnalysis


@dataclass(frozen=True)
class ChangeSummary:
    period: AnalysisPeriod
    full_period: MorningAfternoonWindow
    recent_8w: MorningAfternoonWindow
    recent_16w: MorningAfternoonWindow
    preceding_16w: MorningAfternoonWindow
    recent_16w_vs_preceding_16w: MorningAfternoonWindowComparison
    weekly: WeeklyMorningAfternoonAnalysis


@dataclass(frozen=True)
class StuckTaskSummary:
    period: AnalysisPeriod
    total_stuck_sessions: int
    total_distinct_stuck_tasks: int
    returned_task_count: int
    limit: int
    tasks: tuple[StuckTaskObservation, ...]
