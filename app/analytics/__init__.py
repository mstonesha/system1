"""Deterministic production analytics.

This package is the calculation layer. It must not import
evaluation code, and it does not interpret results (no
recommendations, significance, or best-day fields).
"""

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
    DaypartOutcomeGroup,
    InterruptionOutcomeGroup,
    OutcomeAnalysis,
    TaskAgeAnalysis,
    TaskAgeBucket,
    TerminalTaskAge,
    WeekdayDaypartOutcomeGroup,
    WeekdayOutcomeGroup,
)

__all__ = [
    "COMMITTED_OUTCOMES",
    "DAYPARTS",
    "DAYPART_NAMES",
    "EXECUTION_AGE_BUCKETS",
    "NEGATIVE_OUTCOMES",
    "POSITIVE_OUTCOMES",
    "DaypartOutcomeGroup",
    "InterruptionOutcomeGroup",
    "OutcomeAnalysis",
    "TaskAgeAnalysis",
    "TaskAgeBucket",
    "TerminalTaskAge",
    "WeekdayDaypartOutcomeGroup",
    "WeekdayOutcomeGroup",
    "classify_daypart",
    "classify_execution_age_days",
    "session_outcomes_by_daypart",
    "session_outcomes_by_interruption",
    "session_outcomes_by_weekday",
    "session_outcomes_by_weekday_daypart",
    "task_abandonment_by_execution_age",
    "terminal_tasks_with_execution_age",
]
