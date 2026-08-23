"""Deterministic production analytics.

This package is the calculation layer. It must not import
evaluation code, and it does not interpret results (no
recommendations, significance, or best-day fields).
"""

from app.analytics.change import (
    compare_morning_afternoon_windows,
    morning_afternoon_window,
    rolling_window_dates,
    weekly_morning_afternoon_outcomes,
)
from app.analytics.dayparts import (
    DAYPART_NAMES,
    DAYPARTS,
    classify_daypart,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_interruption,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)
from app.analytics.planning import (
    daily_planning_summary,
    task_effort_estimation,
    weekly_workload,
)
from app.analytics.task_age import (
    EXECUTION_AGE_BUCKETS,
    classify_execution_age_days,
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.analytics.types import (
    COMMITTED_OUTCOMES,
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    DailyPlanningSummary,
    DaypartOutcomeGroup,
    InterruptionOutcomeGroup,
    MorningAfternoonWindow,
    MorningAfternoonWindowComparison,
    OutcomeAnalysis,
    TaskAgeAnalysis,
    TaskAgeBucket,
    TaskEffortEstimation,
    TerminalTaskAge,
    WeekdayDaypartOutcomeGroup,
    WeekdayOutcomeGroup,
    WeeklyMorningAfternoonAnalysis,
    WeeklyMorningAfternoonGroup,
    WeeklyWorkloadAnalysis,
    WeeklyWorkloadGroup,
)

__all__ = [
    "COMMITTED_OUTCOMES",
    "DAYPARTS",
    "DAYPART_NAMES",
    "EXECUTION_AGE_BUCKETS",
    "NEGATIVE_OUTCOMES",
    "POSITIVE_OUTCOMES",
    "DailyPlanningSummary",
    "DaypartOutcomeGroup",
    "InterruptionOutcomeGroup",
    "MorningAfternoonWindow",
    "MorningAfternoonWindowComparison",
    "OutcomeAnalysis",
    "TaskAgeAnalysis",
    "TaskAgeBucket",
    "TaskEffortEstimation",
    "TerminalTaskAge",
    "WeekdayDaypartOutcomeGroup",
    "WeekdayOutcomeGroup",
    "WeeklyMorningAfternoonAnalysis",
    "WeeklyMorningAfternoonGroup",
    "WeeklyWorkloadAnalysis",
    "WeeklyWorkloadGroup",
    "classify_daypart",
    "classify_execution_age_days",
    "compare_morning_afternoon_windows",
    "daily_planning_summary",
    "morning_afternoon_window",
    "rolling_window_dates",
    "session_outcomes_by_daypart",
    "session_outcomes_by_interruption",
    "session_outcomes_by_weekday",
    "session_outcomes_by_weekday_daypart",
    "task_abandonment_by_execution_age",
    "task_effort_estimation",
    "terminal_tasks_with_execution_age",
    "weekly_morning_afternoon_outcomes",
    "weekly_workload",
]
