"""Pydantic response models for the analytical HTTP API.

Field names and meanings match ``app.analysis`` contracts.
This module maps dataclasses to JSON; it does not calculate
or interpret analytics.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict


class _AnalysisModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AnalysisPeriodModel(_AnalysisModel):
    from_date: date
    to_date: date
    timezone: str


class WeekdayOutcomeGroupModel(_AnalysisModel):
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


class DaypartOutcomeGroupModel(_AnalysisModel):
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


class WeekdayDaypartOutcomeGroupModel(_AnalysisModel):
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


class InterruptionOutcomeGroupModel(_AnalysisModel):
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


class MorningAfternoonWindowModel(_AnalysisModel):
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


class MorningAfternoonWindowComparisonModel(_AnalysisModel):
    current: MorningAfternoonWindowModel
    baseline: MorningAfternoonWindowModel
    morning_rate_change: float | None
    afternoon_rate_change: float | None
    gap_change: float | None


class WeeklyMorningAfternoonGroupModel(_AnalysisModel):
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


class WeeklyMorningAfternoonAnalysisModel(_AnalysisModel):
    from_date: date
    to_date: date
    timezone: str
    groups: list[WeeklyMorningAfternoonGroupModel]


class TaskAgeBucketModel(_AnalysisModel):
    age_bucket: str
    min_days: int
    max_days: int | None
    terminal_task_count: int
    completed_count: int
    abandoned_count: int
    abandonment_rate: float
    completion_rate: float


class TerminalTaskAgeModel(_AnalysisModel):
    task_id: int
    first_today_date: date
    terminal_date: date
    execution_age_days: int
    terminal_outcome: str


class DailyPlanningSummaryModel(_AnalysisModel):
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


class TaskEffortEstimationModel(_AnalysisModel):
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


class WeeklyWorkloadGroupModel(_AnalysisModel):
    week_start_date: date
    daily_task_count: int
    daily_tasks_without_explicit_plan: int
    planned_sessions: int
    actual_sessions: int
    positive_sessions: int
    negative_sessions: int
    positive_rate: float | None
    negative_rate: float | None


class WeeklyWorkloadAnalysisModel(_AnalysisModel):
    from_date: date
    to_date: date
    timezone: str
    groups: list[WeeklyWorkloadGroupModel]


class StuckTaskObservationModel(_AnalysisModel):
    task_id: int
    title: str
    stuck_session_count: int
    first_stuck_date: date
    last_stuck_date: date
    task_status: str


class TemporalSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    total_sessions: int
    by_weekday: list[WeekdayOutcomeGroupModel]
    by_daypart: list[DaypartOutcomeGroupModel]
    by_weekday_daypart: list[WeekdayDaypartOutcomeGroupModel]
    morning_afternoon: MorningAfternoonWindowModel


class InterruptionSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    total_sessions: int
    groups: list[InterruptionOutcomeGroupModel]


class TaskAgeSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    total_terminal_tasks: int
    buckets: list[TaskAgeBucketModel]


class TerminalTaskAgeDrilldownResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    limit: int
    returned_task_count: int
    tasks: list[TerminalTaskAgeModel]


class PlanningSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    daily_planning: DailyPlanningSummaryModel
    task_effort: TaskEffortEstimationModel
    weekly_workload: WeeklyWorkloadAnalysisModel


class ChangeSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    full_period: MorningAfternoonWindowModel
    recent_8w: MorningAfternoonWindowModel
    recent_16w: MorningAfternoonWindowModel
    preceding_16w: MorningAfternoonWindowModel
    recent_16w_vs_preceding_16w: (
        MorningAfternoonWindowComparisonModel
    )
    weekly: WeeklyMorningAfternoonAnalysisModel


class StuckTaskSummaryResponse(_AnalysisModel):
    period: AnalysisPeriodModel
    total_stuck_sessions: int
    total_distinct_stuck_tasks: int
    returned_task_count: int
    limit: int
    tasks: list[StuckTaskObservationModel]
