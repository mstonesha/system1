import ast
import dataclasses
import inspect
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.analysis import (
    AnalysisPeriod,
    get_change_summary,
    get_interruption_summary,
    get_planning_summary,
    get_stuck_task_drilldown,
    get_task_age_summary,
    get_temporal_summary,
    get_terminal_task_age_drilldown,
)
from app.analysis.types import (
    ChangeSummary,
    InterruptionSummary,
    PlanningSummary,
    StuckTaskSummary,
    TaskAgeSummary,
    TemporalSummary,
    TerminalTaskAgeDrilldown,
)
from app.analytics.change import (
    compare_morning_afternoon_windows,
    morning_afternoon_window,
    rolling_window_dates,
    weekly_morning_afternoon_outcomes,
)
from app.analytics.drilldown import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    stuck_task_drilldown,
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
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = REPO_ROOT / "app" / "analysis"
ANALYTICS_DIR = REPO_ROOT / "app" / "analytics"
FROM_DATE = date(2026, 1, 12)
TO_DATE = date(2026, 1, 18)
INTERPRETIVE_FIELDS = {
    "recommendation",
    "conclusion",
    "pattern",
    "hypothesis",
    "significance",
    "confidence",
    "best_time",
    "overload",
    "risk_score",
    "interpretation",
    "best_weekday",
    "best_daypart",
    "regime",
    "trend",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "evaluation",
    "app.routes",
    "app.main",
    "fastapi",
    "openai",
    "httpx",
)


def _local(year, month, day, hour, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=LONDON,
    )


def _add_completed(
    db,
    make_task,
    make_daily_task,
    local_started,
    outcome="progress",
    interrupted=False,
    title="Contract task",
):
    task = make_task(title)
    daily_task = make_daily_task(
        task,
        target_date=local_started.date(),
    )
    started_at = local_started.astimezone(UTC)
    work_session = WorkSession(
        daily_task_id=daily_task.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state="completed",
        outcome=outcome,
        interrupted=interrupted,
    )
    db.add(work_session)
    db.commit()
    db.refresh(work_session)
    return task, daily_task, work_session


def _complete_task_on(db, task, daily, local_started):
    started_at = local_started.astimezone(UTC)
    work = WorkSession(
        daily_task_id=daily.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state="completed",
        outcome="complete",
    )
    db.add(work)
    daily.state = "completed"
    task.status = "completed"
    task.completed_at = started_at + timedelta(minutes=25)
    db.commit()
    db.refresh(task)
    db.refresh(daily)


def _snapshot(db):
    tasks = [
        (
            row.id,
            row.title,
            row.status,
            row.estimated_sessions,
            row.completed_at,
        )
        for row in db.query(Task).order_by(Task.id)
    ]
    dailies = [
        (
            row.id,
            row.task_id,
            row.date,
            row.state,
            row.planned_sessions,
        )
        for row in db.query(DailyTask).order_by(DailyTask.id)
    ]
    sessions = [
        (
            row.id,
            row.daily_task_id,
            row.session_state,
            row.outcome,
            row.interrupted,
            row.started_at,
            row.ended_at,
        )
        for row in db.query(WorkSession).order_by(
            WorkSession.id
        )
    ]
    return tasks, dailies, sessions


def _seed_contract_data(db, make_task, make_daily_task):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 10),
        "progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 14),
        "stuck",
        interrupted=True,
        title="Stuck contract task",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 14, 16),
        "complete",
    )
    aged = make_task(
        "Aged completed",
        estimated_sessions=2,
    )
    aged_daily = make_daily_task(
        aged,
        target_date=date(2026, 1, 12),
        planned_sessions=2,
    )
    later_daily = make_daily_task(
        aged,
        target_date=date(2026, 1, 15),
        planned_sessions=2,
    )
    _complete_task_on(
        db,
        aged,
        later_daily,
        _local(2026, 1, 15, 10),
    )


def test_analysis_package_imports_cleanly():
    import app.analysis as analysis

    assert callable(analysis.get_temporal_summary)
    assert callable(analysis.get_interruption_summary)
    assert callable(analysis.get_task_age_summary)
    assert callable(analysis.get_planning_summary)
    assert callable(analysis.get_change_summary)
    assert callable(analysis.get_stuck_task_drilldown)
    assert callable(analysis.get_terminal_task_age_drilldown)


def test_valid_period_is_accepted(db):
    summary = get_temporal_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.period == AnalysisPeriod(
        from_date=FROM_DATE,
        to_date=TO_DATE,
        timezone=get_settings().timezone,
    )


def test_reversed_date_range_is_rejected(db):
    with pytest.raises(ValueError, match="from_date"):
        get_temporal_summary(
            db,
            from_date=TO_DATE,
            to_date=FROM_DATE,
        )
    with pytest.raises(ValueError, match="from_date"):
        get_change_summary(
            db,
            from_date=date(2026, 2, 1),
            to_date=date(2026, 1, 1),
        )


def test_timezone_matches_app_timezone(db):
    summary = get_planning_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.period.timezone == get_settings().timezone
    assert summary.period.timezone == "Europe/London"


def test_temporal_summary_matches_production(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    weekday = session_outcomes_by_weekday(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    daypart = session_outcomes_by_daypart(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    cells = session_outcomes_by_weekday_daypart(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    morning_afternoon = morning_afternoon_window(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    summary = get_temporal_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.by_weekday == weekday.groups
    assert summary.by_daypart == daypart.groups
    assert summary.by_weekday_daypart == cells.groups
    assert summary.morning_afternoon == morning_afternoon
    assert summary.total_sessions == weekday.total_sessions
    assert summary.total_sessions == daypart.total_sessions


def test_interruption_summary_matches_production(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    production = session_outcomes_by_interruption(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    summary = get_interruption_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.groups == production.groups
    assert summary.total_sessions == production.total_sessions


def test_task_age_summary_matches_production(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    production = task_abandonment_by_execution_age(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    summary = get_task_age_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.buckets == production.groups
    assert summary.total_terminal_tasks == (
        production.total_terminal_tasks
    )
    assert not hasattr(summary, "tasks")


def test_terminal_task_age_drilldown_is_bounded(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    production = terminal_tasks_with_execution_age(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        limit=DEFAULT_LIMIT,
    )
    summary = get_terminal_task_age_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.tasks == production
    assert summary.limit == DEFAULT_LIMIT
    assert summary.returned_task_count == len(production)
    with pytest.raises(ValueError, match="limit"):
        get_terminal_task_age_drilldown(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
            limit=MAX_LIMIT + 1,
        )
    with pytest.raises(ValueError, match="limit"):
        get_terminal_task_age_drilldown(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
            limit=0,
        )


def test_planning_summary_keeps_three_subfamilies(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    daily = daily_planning_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    effort = task_effort_estimation(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    weekly = weekly_workload(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    summary = get_planning_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.daily_planning == daily
    assert summary.task_effort == effort
    assert summary.weekly_workload == weekly
    assert set(summary.__dataclass_fields__) == {
        "period",
        "daily_planning",
        "task_effort",
        "weekly_workload",
    }


def test_change_summary_matches_production_preset(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    recent_8w = rolling_window_dates(TO_DATE, weeks=8)
    recent_16w = rolling_window_dates(TO_DATE, weeks=16)
    preceding_16w = rolling_window_dates(
        recent_16w[0] - timedelta(days=1),
        weeks=16,
    )
    production_full = morning_afternoon_window(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    production_8w = morning_afternoon_window(
        db,
        from_date=recent_8w[0],
        to_date=recent_8w[1],
    )
    production_16w = morning_afternoon_window(
        db,
        from_date=recent_16w[0],
        to_date=recent_16w[1],
    )
    production_preceding = morning_afternoon_window(
        db,
        from_date=preceding_16w[0],
        to_date=preceding_16w[1],
    )
    production_compare = compare_morning_afternoon_windows(
        db,
        current_from_date=recent_16w[0],
        current_to_date=recent_16w[1],
        baseline_from_date=preceding_16w[0],
        baseline_to_date=preceding_16w[1],
    )
    production_weekly = weekly_morning_afternoon_outcomes(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    summary = get_change_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert summary.full_period == production_full
    assert summary.recent_8w == production_8w
    assert summary.recent_16w == production_16w
    assert summary.preceding_16w == production_preceding
    assert summary.recent_16w_vs_preceding_16w == (
        production_compare
    )
    assert summary.weekly == production_weekly


def test_stuck_drilldown_matches_production_bounds(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    defaulted = get_stuck_task_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    production = stuck_task_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    assert defaulted.limit == DEFAULT_LIMIT
    assert defaulted.tasks == production.tasks
    assert defaulted.total_stuck_sessions == (
        production.total_stuck_sessions
    )
    assert defaulted.total_distinct_stuck_tasks == (
        production.total_distinct_stuck_tasks
    )
    assert defaulted.returned_task_count == (
        production.returned_task_count
    )
    limited = get_stuck_task_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        limit=1,
    )
    assert limited.limit == 1
    assert limited.returned_task_count <= 1
    with pytest.raises(ValueError, match="limit"):
        get_stuck_task_drilldown(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
            limit=MAX_LIMIT + 1,
        )


def test_contract_objects_have_no_interpretive_fields():
    for cls in (
        AnalysisPeriod,
        TemporalSummary,
        InterruptionSummary,
        TaskAgeSummary,
        TerminalTaskAgeDrilldown,
        PlanningSummary,
        ChangeSummary,
        StuckTaskSummary,
    ):
        names = set(cls.__dataclass_fields__)
        assert INTERPRETIVE_FIELDS.isdisjoint(names), cls


def test_public_functions_have_no_sql_argument():
    for func in (
        get_temporal_summary,
        get_interruption_summary,
        get_task_age_summary,
        get_planning_summary,
        get_change_summary,
        get_stuck_task_drilldown,
        get_terminal_task_age_drilldown,
    ):
        names = set(inspect.signature(func).parameters)
        assert "sql" not in names
        assert "query" not in names
        assert "statement" not in names


def test_analysis_does_not_import_evaluation_or_http():
    for path in ANALYSIS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not any(
                    name == prefix
                    or name.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ), (path, name)


def test_analytics_does_not_import_analysis():
    for path in ANALYTICS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name != "app.analysis"
                assert not name.startswith(
                    "app.analysis."
                ), path


def test_representative_calls_do_not_mutate(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    before = _snapshot(db)
    get_temporal_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_interruption_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_task_age_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_planning_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_change_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_stuck_task_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    get_terminal_task_age_drilldown(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    db.expire_all()
    assert _snapshot(db) == before
    assert not db.new
    assert not db.deleted
    assert not db.dirty


def test_contract_objects_are_asdict_serializable(
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    summary = get_temporal_summary(
        db,
        from_date=FROM_DATE,
        to_date=TO_DATE,
    )
    payload = dataclasses.asdict(summary)
    assert payload["period"]["from_date"] == FROM_DATE
    assert payload["period"]["to_date"] == TO_DATE
    assert isinstance(payload["by_weekday"], tuple)
    assert all(
        isinstance(row, dict) for row in payload["by_weekday"]
    )
    planning = dataclasses.asdict(
        get_planning_summary(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert "daily_planning" in planning
    assert "task_effort" in planning
    assert "weekly_workload" in planning
