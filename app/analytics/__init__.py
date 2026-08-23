"""Deterministic analytical queries over WorkSession outcomes.

This package is the production calculation layer. It must
not import evaluation code, and it does not interpret
results (no recommendations, significance, or best-day
fields).
"""

from app.analytics.dayparts import (
    DAYPART_NAMES,
    DAYPARTS,
    classify_daypart,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)
from app.analytics.types import (
    COMMITTED_OUTCOMES,
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    DaypartOutcomeGroup,
    OutcomeAnalysis,
    WeekdayDaypartOutcomeGroup,
    WeekdayOutcomeGroup,
)

__all__ = [
    "COMMITTED_OUTCOMES",
    "DAYPARTS",
    "DAYPART_NAMES",
    "NEGATIVE_OUTCOMES",
    "POSITIVE_OUTCOMES",
    "DaypartOutcomeGroup",
    "OutcomeAnalysis",
    "WeekdayDaypartOutcomeGroup",
    "WeekdayOutcomeGroup",
    "classify_daypart",
    "session_outcomes_by_daypart",
    "session_outcomes_by_weekday",
    "session_outcomes_by_weekday_daypart",
]
