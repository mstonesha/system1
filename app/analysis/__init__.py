"""Production analytical contract.

This package is the approved read-only boundary over
``app.analytics``. It does not interpret results, call
models, or expose arbitrary SQL.
"""

from app.analytics.drilldown import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
)
from app.analysis.service import (
    get_change_summary,
    get_interruption_summary,
    get_planning_summary,
    get_stuck_task_drilldown,
    get_task_age_summary,
    get_temporal_summary,
    get_terminal_task_age_drilldown,
)
from app.analysis.types import (
    AnalysisPeriod,
    ChangeSummary,
    InterruptionSummary,
    PlanningSummary,
    StuckTaskSummary,
    TaskAgeSummary,
    TemporalSummary,
    TerminalTaskAgeDrilldown,
)

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "AnalysisPeriod",
    "ChangeSummary",
    "InterruptionSummary",
    "PlanningSummary",
    "StuckTaskSummary",
    "TaskAgeSummary",
    "TemporalSummary",
    "TerminalTaskAgeDrilldown",
    "get_change_summary",
    "get_interruption_summary",
    "get_planning_summary",
    "get_stuck_task_drilldown",
    "get_task_age_summary",
    "get_temporal_summary",
    "get_terminal_task_age_drilldown",
]
