import ast
import hashlib
import inspect
import json
import urllib.error
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.models import DailyTask, WorkSession
from app.time import UTC
from evaluation.agent.evidence import (
    build_change_evidence,
    build_dependencies_evidence,
    build_evidence,
    build_planning_evidence,
    build_task_age_evidence,
    build_temporal_evidence,
    evidence_ids,
    serialize_evidence,
)
from evaluation.agent.model import (
    API_KEY_ENV,
    BASE_URL_ENV,
    ERROR_BODY_LIMIT,
    MODEL_ENV,
    OpenAICompatibleModel,
    StubAnalysisModel,
    load_configured_model,
)
from evaluation.agent.prompt import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_VERSION,
    PROMPT_VERSION_CHANGE_V1,
    PROMPT_VERSION_DEPENDENCIES_V1,
    PROMPT_VERSION_PLANNING_V1,
    PROMPT_VERSION_TASK_AGE_V1,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    PROMPT_VERSION_V3,
    SYSTEM_INSTRUCTIONS_CHANGE_V1,
    SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1,
    SYSTEM_INSTRUCTIONS_PLANNING_V1,
    SYSTEM_INSTRUCTIONS_TASK_AGE_V1,
    SYSTEM_INSTRUCTIONS_V1,
    SYSTEM_INSTRUCTIONS_V2,
    SYSTEM_INSTRUCTIONS_V3,
    ModelPrompt,
    render_prompt,
)
from evaluation.agent.runner import run_case
from evaluation.agent.store import persist_run
from evaluation.agent.types import parse_agent_analysis


LONDON = ZoneInfo("Europe/London")
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
AGENT_DIR = REPO_ROOT / "evaluation" / "agent"
FORBIDDEN_IN_MODEL_INPUT = (
    "temporal_patterns",
    "noise_control",
    "interruptions_dependencies",
    "task_age_abandonment",
    "planning_workload",
    "behaviour_change",
    "dataset a",
    "dataset b",
    "dataset c",
    "dataset d",
    "dataset e",
    "dataset f",
    "ground_truth",
    "expected_patterns",
    "expected_non_patterns",
    "agent_should",
    "agent_expected",
    "generator_truth",
    "not_agent_evaluable",
    "hr_tasks_have_materially_higher_stuck_rate",
    "monday_morning_is_a_strong_negative_exception",
    "no_robust_behavioural_pattern",
    "heavy_weeks",
    "heavy_week",
    "normal_weeks",
    "decoy_week",
    "decoy",
    "historical_phase",
    "transition_phase",
    "recent_phase",
)
VALID_ANALYSIS = {
    "observations": [
        {
            "statement": "Friday has the highest weekday rate.",
            "evidence_refs": ["weekday:friday"],
        }
    ],
    "patterns": [
        {
            "statement": "No robust weekday pattern is supported.",
            "evidence_refs": [
                "weekday:monday",
                "weekday:friday",
            ],
        }
    ],
    "hypotheses": [],
    "insufficient_evidence": [
        {
            "statement": "Evening n is too small to rank as best.",
            "evidence_refs": ["daypart:evening"],
        }
    ],
    "suggested_drilldowns": [],
}


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
    *,
    interrupted=False,
    title=None,
    task=None,
):
    if task is None:
        task = (
            make_task(title=title)
            if title is not None
            else make_task()
        )
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
    return work_session


def _seed_temporal_sessions(db, make_task, make_daily_task):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 18, 30),
        outcome="complete",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 7, 0),
        outcome="progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 10, 0),
        outcome="progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 13, 0),
        outcome="stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 16, 10, 0),
        outcome="complete",
    )


def _seed_dependency_sessions(db, make_task, make_daily_task):
    waiting = make_task(title="Await approval for budget sign-off")
    notes = make_task(title="Write weekly notes")
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 10),
        outcome="progress",
        interrupted=False,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 11),
        outcome="complete",
        interrupted=False,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 10),
        outcome="stuck",
        interrupted=True,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 14),
        outcome="paused",
        interrupted=True,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 9),
        outcome="stuck",
        task=waiting,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 9),
        outcome="stuck",
        task=waiting,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 14, 9),
        outcome="stuck",
        task=waiting,
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 14, 14),
        outcome="stuck",
        task=notes,
    )
    return waiting, notes


def _add_daily(
    db,
    task,
    target_date,
    state="planned",
    planned_sessions=1,
):
    daily = DailyTask(
        task_id=task.id,
        date=target_date,
        planned_sessions=planned_sessions,
        state=state,
        sort_order=0,
    )
    db.add(daily)
    db.commit()
    db.refresh(daily)
    return daily


def _session_on(db, daily, local_started, outcome="progress"):
    started_at = local_started.astimezone(UTC)
    work = WorkSession(
        daily_task_id=daily.id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=25),
        planned_duration_seconds=1500,
        actual_duration_seconds=1500,
        session_state="completed",
        outcome=outcome,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _complete_task_on(db, task, daily, local_started):
    started_at = local_started.astimezone(UTC)
    db.add(
        WorkSession(
            daily_task_id=daily.id,
            started_at=started_at,
            ended_at=started_at + timedelta(minutes=25),
            planned_duration_seconds=1500,
            actual_duration_seconds=1500,
            session_state="completed",
            outcome="complete",
        )
    )
    daily.state = "completed"
    task.status = "completed"
    task.completed_at = (
        local_started + timedelta(minutes=25)
    ).astimezone(UTC)
    db.commit()
    db.refresh(task)
    db.refresh(daily)


def _abandon_task_on(db, task, daily, local_started):
    started_at = local_started.astimezone(UTC)
    db.add(
        WorkSession(
            daily_task_id=daily.id,
            started_at=started_at,
            ended_at=started_at + timedelta(minutes=25),
            planned_duration_seconds=1500,
            actual_duration_seconds=1500,
            session_state="completed",
            outcome="abandoned",
        )
    )
    daily.state = "abandoned"
    task.status = "cancelled"
    task.completed_at = None
    db.commit()
    db.refresh(task)
    db.refresh(daily)


def _seed_task_age_terminals(db, make_task):
    """Terminal tasks spanning production execution-age buckets."""
    young_created = make_task(title="Recently planned")
    young_created.created_at = _local(2025, 1, 1, 9).astimezone(UTC)
    db.commit()
    first = _add_daily(db, young_created, date(2026, 1, 14))
    _complete_task_on(
        db,
        young_created,
        first,
        _local(2026, 1, 14, 10),
    )

    age0 = make_task()
    d0 = _add_daily(db, age0, date(2026, 1, 12))
    _complete_task_on(db, age0, d0, _local(2026, 1, 12, 10))

    age1 = make_task()
    _add_daily(db, age1, date(2026, 1, 12))
    last1 = _add_daily(db, age1, date(2026, 1, 13))
    _abandon_task_on(db, age1, last1, _local(2026, 1, 13, 10))

    age5 = make_task()
    _add_daily(db, age5, date(2026, 1, 12))
    last5 = _add_daily(db, age5, date(2026, 1, 17))
    _complete_task_on(db, age5, last5, _local(2026, 1, 17, 10))

    age10 = make_task()
    _add_daily(db, age10, date(2026, 1, 12))
    last10 = _add_daily(db, age10, date(2026, 1, 22))
    _abandon_task_on(db, age10, last10, _local(2026, 1, 22, 10))

    age20 = make_task()
    _add_daily(db, age20, date(2026, 1, 12))
    last20 = _add_daily(db, age20, date(2026, 2, 1))
    _complete_task_on(db, age20, last20, _local(2026, 2, 1, 10))

    age40 = make_task()
    _add_daily(db, age40, date(2025, 12, 1))
    last40 = _add_daily(db, age40, date(2026, 1, 16))
    _abandon_task_on(db, age40, last40, _local(2026, 1, 16, 10))

    age35_complete = make_task()
    _add_daily(db, age35_complete, date(2025, 12, 8))
    last35 = _add_daily(db, age35_complete, date(2026, 1, 12))
    _complete_task_on(
        db,
        age35_complete,
        last35,
        _local(2026, 1, 12, 11),
    )
    return young_created


def _seed_planning_case(db, make_task):
    """Planning, effort, and two-week workload rows."""
    unfinished = make_task()
    unfinished_daily = _add_daily(
        db,
        unfinished,
        date(2026, 3, 2),
        planned_sessions=4,
    )
    _session_on(
        db,
        unfinished_daily,
        _local(2026, 3, 2, 10),
        "progress",
    )
    _session_on(
        db,
        unfinished_daily,
        _local(2026, 3, 2, 11),
        "progress",
    )

    early = make_task()
    early_daily = _add_daily(
        db,
        early,
        date(2026, 3, 3),
        planned_sessions=4,
    )
    _session_on(db, early_daily, _local(2026, 3, 3, 10), "progress")
    _complete_task_on(db, early, early_daily, _local(2026, 3, 3, 11))

    abandoned = make_task()
    abandoned_daily = _add_daily(
        db,
        abandoned,
        date(2026, 3, 4),
        planned_sessions=4,
    )
    _session_on(
        db,
        abandoned_daily,
        _local(2026, 3, 4, 10),
        "progress",
    )
    _abandon_task_on(
        db,
        abandoned,
        abandoned_daily,
        _local(2026, 3, 4, 11),
    )

    extra = make_task()
    extra_daily = _add_daily(
        db,
        extra,
        date(2026, 3, 5),
        planned_sessions=1,
    )
    _session_on(db, extra_daily, _local(2026, 3, 5, 10), "progress")
    _session_on(db, extra_daily, _local(2026, 3, 5, 11), "progress")

    implicit = make_task()
    implicit_daily = _add_daily(
        db,
        implicit,
        date(2026, 3, 6),
        planned_sessions=None,
    )
    _session_on(
        db,
        implicit_daily,
        _local(2026, 3, 6, 10),
        "progress",
    )

    removed = make_task()
    _add_daily(
        db,
        removed,
        date(2026, 3, 2),
        planned_sessions=9,
        state="removed",
    )

    above = make_task(estimated_sessions=2)
    above_first = _add_daily(
        db,
        above,
        date(2026, 3, 2),
        planned_sessions=2,
    )
    _session_on(db, above_first, _local(2026, 3, 2, 14), "progress")
    above_last = _add_daily(
        db,
        above,
        date(2026, 3, 3),
        planned_sessions=2,
    )
    _session_on(db, above_last, _local(2026, 3, 3, 14), "progress")
    _complete_task_on(db, above, above_last, _local(2026, 3, 3, 15))

    near = make_task(estimated_sessions=2)
    near_daily = _add_daily(
        db,
        near,
        date(2026, 3, 4),
        planned_sessions=2,
    )
    _session_on(db, near_daily, _local(2026, 3, 4, 14), "progress")
    _complete_task_on(db, near, near_daily, _local(2026, 3, 4, 15))

    below = make_task(estimated_sessions=4)
    below_daily = _add_daily(
        db,
        below,
        date(2026, 3, 5),
        planned_sessions=2,
    )
    _session_on(db, below_daily, _local(2026, 3, 5, 14), "progress")
    _complete_task_on(db, below, below_daily, _local(2026, 3, 5, 15))

    load = make_task()
    for day in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)):
        daily = _add_daily(db, load, day, planned_sessions=5)
        _session_on(
            db,
            daily,
            _local(day.year, day.month, day.day, 10),
            "stuck",
        )


def _seed_change_sessions(db, make_task, make_daily_task):
    """Morning/afternoon sessions across earlier and later weeks."""
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
        _local(2026, 1, 12, 14),
        "stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 10),
        "complete",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 15),
        "paused",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 4, 6, 10),
        "stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 4, 6, 14),
        "progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 4, 20, 10),
        "progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 4, 20, 14),
        "stuck",
    )


def _assert_no_model_input_leak(text: str) -> None:
    lowered = text.lower()
    for token in FORBIDDEN_IN_MODEL_INPUT:
        assert token.lower() not in lowered, token


def test_application_code_does_not_import_evaluation():
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(
                        "evaluation"
                    ), path
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(
                    "evaluation"
                ), path


def test_evidence_module_uses_production_analytics():
    text = (AGENT_DIR / "evidence.py").read_text(encoding="utf-8")
    source = inspect.getsource(build_temporal_evidence)
    assert "from app.analytics.outcomes import" in text
    assert "from app.analytics.change import" in text
    assert "session_outcomes_by_weekday" in source
    assert "session_outcomes_by_daypart" in source
    assert "session_outcomes_by_weekday_daypart" in source
    assert "morning_afternoon_window" in source
    assert "weekly_morning_afternoon_outcomes" in text
    assert "session_outcomes_by_interruption" in text
    assert "stuck_task_drilldown" in text
    assert "task_abandonment_by_execution_age" in text
    assert "terminal_tasks_with_execution_age" in text
    assert "daily_planning_summary" in text
    assert "task_effort_estimation" in text
    assert "weekly_workload" in text
    assert "compare_morning_afternoon_windows" in text
    assert "rolling_window_dates" in text
    assert "evaluation.observe" not in text
    assert "yaml.safe_load" not in text
    assert "ground_truth" not in text
    assert "temporal_patterns" not in text
    assert "noise_control" not in text
    assert "interruptions_dependencies" not in text
    assert "task_age_abandonment" not in text
    assert "planning_workload" not in text
    assert "behaviour_change" not in text
    assert "parse_task_category" not in text
    for path in AGENT_DIR.rglob("*.py"):
        agent_text = path.read_text(encoding="utf-8")
        assert "parse_task_category" not in agent_text, path
        assert "evaluation.catalog" not in agent_text, path


def test_prompt_and_run_case_do_not_load_ground_truth():
    prompt_text = (AGENT_DIR / "prompt.py").read_text(
        encoding="utf-8"
    )
    run_source = inspect.getsource(run_case)
    assert "yaml.safe_load" not in prompt_text
    assert "ground_truth" not in prompt_text
    assert "temporal_patterns" not in prompt_text
    assert "noise_control" not in prompt_text
    assert "interruptions_dependencies" not in prompt_text
    assert "task_age_abandonment" not in prompt_text
    assert "planning_workload" not in prompt_text
    assert "behaviour_change" not in prompt_text
    assert "ground_truth" not in run_source
    assert "yaml" not in run_source


def test_evidence_builder_is_deterministic_and_keeps_small_n(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    kwargs = dict(
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_a",
    )
    first = build_temporal_evidence(db, **kwargs)
    second = build_temporal_evidence(db, **kwargs)
    assert serialize_evidence(first) == serialize_evidence(second)
    assert first["case_id"] == "case_a"
    assert first["period"]["total_sessions"] == 5
    assert first["period"]["timezone"] == "Europe/London"
    monday = next(
        row
        for row in first["weekday_outcomes"]
        if row["weekday"] == "Monday"
    )
    assert monday["n"] == 2
    assert monday["id"] == "weekday:monday"
    assert monday["complete"] == 1
    assert monday["progress"] == 1
    evening = next(
        row
        for row in first["daypart_outcomes"]
        if row["daypart"] == "evening"
    )
    assert evening["n"] == 1
    assert evening["positive_rate"] == 1.0
    monday_evening = next(
        row
        for row in first["weekday_daypart_outcomes"]
        if row["id"] == "weekday_daypart:monday:evening"
    )
    assert monday_evening["n"] == 1
    window = first["morning_afternoon"]
    assert window["morning"]["n"] == 2
    assert window["afternoon"]["n"] == 1
    blob = serialize_evidence(first)
    _assert_no_model_input_leak(blob)
    assert "interruption" not in first
    assert "stuck_task" not in blob
    assert "planned_sessions" not in blob
    assert set(first) == {
        "case_id",
        "period",
        "weekday_outcomes",
        "daypart_outcomes",
        "weekday_daypart_outcomes",
        "morning_afternoon",
    }
    assert "weekly_morning_afternoon" not in first


def test_evidence_builder_rejects_semantic_case_id(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=date(2026, 1, 12),
            to_date=date(2026, 1, 16),
            case_id="temporal_patterns",
        )


def test_evidence_builder_calls_production_analytics(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    from evaluation.agent import evidence as evidence_mod

    _seed_temporal_sessions(db, make_task, make_daily_task)
    calls: list[str] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_weekday",
        wrap(
            "weekday",
            evidence_mod.session_outcomes_by_weekday,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_daypart",
        wrap(
            "daypart",
            evidence_mod.session_outcomes_by_daypart,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_weekday_daypart",
        wrap(
            "cells",
            evidence_mod.session_outcomes_by_weekday_daypart,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "morning_afternoon_window",
        wrap(
            "window",
            evidence_mod.morning_afternoon_window,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "weekly_morning_afternoon_outcomes",
        wrap(
            "weeks",
            evidence_mod.weekly_morning_afternoon_outcomes,
        ),
    )
    build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
    )
    assert calls == ["weekday", "daypart", "cells", "window"]
    calls.clear()
    v2 = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
        version="temporal-v2",
    )
    assert calls == [
        "weekday",
        "daypart",
        "cells",
        "window",
        "weeks",
    ]
    assert "weekly_morning_afternoon" in v2
    calls.clear()
    v3 = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
        version="temporal-v3",
    )
    assert calls == [
        "weekday",
        "daypart",
        "cells",
        "window",
        "weeks",
    ]
    assert serialize_evidence(v2) == serialize_evidence(v3)


def test_temporal_v1_prompt_is_preserved():
    assert PROMPT_VERSION == "temporal-v1"
    assert DEFAULT_PROMPT_VERSION == "temporal-v1"
    assert SYSTEM_INSTRUCTIONS_V1 is not SYSTEM_INSTRUCTIONS_V2
    assert "This analysis uses contract temporal-v2" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "Cross-sectional consistency" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "It is explicitly acceptable for patterns to be empty" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "no robust pattern is supported" in (
        SYSTEM_INSTRUCTIONS_V1.lower()
    )
    assert "sample size" in SYSTEM_INSTRUCTIONS_V1.lower()
    assert "dependencies-v1" not in SYSTEM_INSTRUCTIONS_V1
    assert "stuck_task_drilldown" not in SYSTEM_INSTRUCTIONS_V1
    prompt = render_prompt({"case_id": "case_a"}, version="temporal-v1")
    assert prompt.version == PROMPT_VERSION_V1
    assert prompt.system == SYSTEM_INSTRUCTIONS_V1


def test_temporal_v2_prompt_is_preserved():
    prompt = render_prompt(
        {"case_id": "case_f"},
        version="temporal-v2",
    )
    assert prompt.version == PROMPT_VERSION_V2
    assert prompt.system == SYSTEM_INSTRUCTIONS_V2
    text = prompt.system.lower()
    assert "this analysis uses contract temporal-v2" in text
    assert "cross-sectional consistency" in text
    assert "temporal stability" in text
    assert "does not establish temporal stability" in text
    assert "effect size" in text
    assert "patterns to be empty" in text
    assert "no robust pattern is supported" in text
    assert "sample size" in text
    assert "best" in text
    assert "converging evidence" not in text
    assert "do not automatically invalidate" not in text
    assert "qualified pattern" not in text
    assert "temporal-v3" not in prompt.system
    assert "dependencies-v1" not in prompt.system
    assert "stuck_task_drilldown" not in prompt.system
    assert "temporal_patterns" not in prompt.system
    assert "noise_control" not in prompt.system
    _assert_no_model_input_leak(prompt.combined_text())


def test_temporal_v3_prompt_uses_converging_evidence():
    prompt = render_prompt(
        {"case_id": "case_a"},
        version="temporal-v3",
    )
    assert prompt.version == PROMPT_VERSION_V3
    assert prompt.system == SYSTEM_INSTRUCTIONS_V3
    text = prompt.system.lower()
    assert "this analysis uses contract temporal-v3" in text
    assert "converging" in text
    assert "do not automatically invalidate" in text
    assert "qualified pattern" in text
    assert "materially differs from comparable peers" in text
    assert "hard temporal-stability rule" in text
    assert "need not be universal" in text
    assert "patterns to be empty" in text
    assert "no robust pattern is supported" in text
    assert "sample size" in text
    assert "best" in text
    assert "do not extrapolate beyond the observed period" in text
    assert "temporal_patterns" not in prompt.system
    assert "noise_control" not in prompt.system
    assert "monday morning" not in text
    assert "dependencies-v1" not in prompt.system
    assert "stuck_task_drilldown" not in prompt.system
    _assert_no_model_input_leak(prompt.combined_text())


FROZEN_PROMPT_SHA256 = {
    "temporal-v1": (
        "dae441068655e1c0388afb78ae3b4096a944fb8f9046c85c3243f0db8b90770c"
    ),
    "temporal-v2": (
        "9be5ced2c7c1dcae87d161478c626fbdbe02f1b3bf690b796ef0cda568d06eb8"
    ),
    "temporal-v3": (
        "926173fbfbc318d67bffc9fa084bdf3df319b99fc9765ab03c73089ff2d99ce8"
    ),
    "dependencies-v1": (
        "b6d6085322266708abfa52231ef55ae3c589763a37138679521620e7991b804d"
    ),
    "task-age-v1": (
        "a30e903d4f9ded5a4c05bd8d3f44b8488071f0bd9e8ee3be85941ef671f82546"
    ),
    "planning-v1": (
        "3d7b141d8f77f7f3cd40668b6724c40dde153be986546916b4dc9b615ad3d100"
    ),
    "change-v1": (
        "8a64d729f74c3864d5f71dbef83db597383fde88b621906a9f8d2bc0b9bd5b7e"
    ),
}


def test_frozen_prompt_contracts_are_byte_stable():
    from evaluation.agent.prompt import (
        SYSTEM_INSTRUCTIONS_CHANGE_V1,
        SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1,
        SYSTEM_INSTRUCTIONS_PLANNING_V1,
    )

    actual = {
        "temporal-v1": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_V1.encode("utf-8")
        ).hexdigest(),
        "temporal-v2": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_V2.encode("utf-8")
        ).hexdigest(),
        "temporal-v3": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_V3.encode("utf-8")
        ).hexdigest(),
        "dependencies-v1": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1.encode("utf-8")
        ).hexdigest(),
        "task-age-v1": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_TASK_AGE_V1.encode("utf-8")
        ).hexdigest(),
        "planning-v1": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_PLANNING_V1.encode("utf-8")
        ).hexdigest(),
        "change-v1": hashlib.sha256(
            SYSTEM_INSTRUCTIONS_CHANGE_V1.encode("utf-8")
        ).hexdigest(),
    }
    assert actual == FROZEN_PROMPT_SHA256


def test_temporal_v2_weekly_evidence_uses_production_rows(
    db,
    make_task,
    make_daily_task,
):
    from app.analytics.change import weekly_morning_afternoon_outcomes

    _seed_temporal_sessions(db, make_task, make_daily_task)
    from_date = date(2026, 1, 12)
    to_date = date(2026, 1, 16)
    production = weekly_morning_afternoon_outcomes(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    v1 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v1",
    )
    v2 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v2",
    )
    assert "weekly_morning_afternoon" not in v1
    weeks = v2["weekly_morning_afternoon"]
    assert len(weeks) == len(production.groups)
    assert production.groups
    for row, group in zip(weeks, production.groups):
        week_start = group.week_start_date.isoformat()
        assert row["id"] == f"week:{week_start}"
        assert row["week_start"] == week_start
        assert row["morning_n"] == group.morning_session_count
        assert row["morning_positive_rate"] == (
            group.morning_positive_rate
        )
        assert row["afternoon_n"] == group.afternoon_session_count
        assert row["afternoon_positive_rate"] == (
            group.afternoon_positive_rate
        )
        assert row["positive_rate_gap"] == group.positive_rate_gap
        assert "stable" not in row
        assert "significance" not in row
    known = evidence_ids(v2)
    assert weeks[0]["id"] in known
    parsed = parse_agent_analysis(
        json.dumps(
            {
                "observations": [
                    {
                        "statement": "A weekly gap is present.",
                        "evidence_refs": [weeks[0]["id"]],
                    }
                ],
                "patterns": [],
                "hypotheses": [],
                "insufficient_evidence": [
                    {
                        "statement": "An unknown week was cited.",
                        "evidence_refs": ["week:1999-01-04"],
                    }
                ],
                "suggested_drilldowns": [],
            }
        ),
        known_ids=known,
    )
    assert parsed.ok
    assert parsed.unknown_evidence_refs == ("week:1999-01-04",)
    prompt = render_prompt(v2, version="temporal-v2")
    assert weeks[0]["id"] in prompt.user
    assert "weekly_morning_afternoon" in prompt.user
    _assert_no_model_input_leak(prompt.combined_text())
    v3 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v3",
    )
    assert serialize_evidence(v2) == serialize_evidence(v3)
    v3_prompt = render_prompt(v3, version="temporal-v3")
    assert v3_prompt.version == "temporal-v3"
    assert serialize_evidence(v3) in v3_prompt.user
    assert v3_prompt.system != prompt.system



def test_prompt_includes_evidence_and_caution_rules(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    evidence = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_a",
    )
    prompt = render_prompt(evidence)
    combined = prompt.combined_text()
    assert prompt.version == PROMPT_VERSION
    assert prompt.version == "temporal-v1"
    assert serialize_evidence(evidence) in prompt.user
    assert "weekly_morning_afternoon" not in prompt.user
    assert "observations" in prompt.system
    assert "patterns" in prompt.system
    assert "hypotheses" in prompt.system
    assert "insufficient_evidence" in prompt.system
    assert "sample size" in prompt.system.lower()
    assert "no robust pattern" in prompt.system.lower()
    assert "best" in prompt.system.lower()
    _assert_no_model_input_leak(combined)


def test_valid_response_parses_and_unknown_refs_are_listed():
    parsed = parse_agent_analysis(
        json.dumps(VALID_ANALYSIS),
        known_ids={"weekday:friday", "daypart:evening"},
    )
    assert parsed.ok
    assert parsed.analysis is not None
    assert parsed.analysis.observations[0].statement.startswith(
        "Friday"
    )
    assert parsed.unknown_evidence_refs == ("weekday:monday",)


def test_missing_required_field_fails_validation():
    payload = dict(VALID_ANALYSIS)
    del payload["patterns"]
    parsed = parse_agent_analysis(json.dumps(payload))
    assert parsed.status == "invalid_schema"
    assert parsed.analysis is None
    assert any("patterns" in error for error in parsed.errors)


def test_malformed_json_is_not_silently_repaired():
    raw = (
        "```json\n"
        + json.dumps(VALID_ANALYSIS)
        + "\n```"
    )
    parsed = parse_agent_analysis(raw)
    assert parsed.status == "invalid_json"
    assert parsed.analysis is None
    assert parsed.raw_text == raw


def test_trailing_comma_is_not_silently_repaired():
    raw = '{"observations": [], "patterns": [],}'
    parsed = parse_agent_analysis(raw)
    assert parsed.status == "invalid_json"
    assert parsed.analysis is None


def test_persist_writes_gitignored_dir_without_secrets(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(API_KEY_ENV, "sk-test-secret-value")
    record = {
        "case_id": "case_a",
        "timestamp": "2026-01-01T00:00:00Z",
        "model": "stub",
        "prompt_version": PROMPT_VERSION,
        "evidence": {"case_id": "case_a"},
        "parse_status": "ok",
    }
    path = persist_run(record, results_dir=tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["case_id"] == "case_a"
    blob = path.read_text(encoding="utf-8")
    assert "sk-test-secret-value" not in blob
    gitignore = (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "evaluation/agent/results/" in gitignore
    with pytest.raises(RuntimeError, match="secret"):
        persist_run(
            {
                "case_id": "case_a",
                "api_key": "sk-test-secret-value",
            },
            results_dir=tmp_path,
        )


def test_load_configured_model_requires_all_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert load_configured_model() is None
    monkeypatch.setenv(API_KEY_ENV, "  ")
    monkeypatch.setenv(BASE_URL_ENV, "https://example.invalid/v1")
    monkeypatch.setenv(MODEL_ENV, "example-model")
    assert load_configured_model() is None
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    model = load_configured_model()
    assert model is not None
    assert model.identifier == "example-model"


def test_run_case_dry_run_and_stub_do_not_need_live_api(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    dry = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
    )
    assert dry["parse_status"] == "dry_run"
    assert dry["prompt_version"] == "temporal-v1"
    assert "weekly_morning_afternoon" not in dry["evidence"]
    assert dry["raw_response"] is None
    _assert_no_model_input_leak(json.dumps(dry["evidence"]))
    _assert_no_model_input_leak(dry["prompt"]["system"])
    _assert_no_model_input_leak(dry["prompt"]["user"])

    stub = StubAnalysisModel(
        json.dumps(
            {
                "observations": [
                    {
                        "statement": "A cited missing slice.",
                        "evidence_refs": ["weekday:sunday"],
                    }
                ],
                "patterns": [],
                "hypotheses": [],
                "insufficient_evidence": [],
                "suggested_drilldowns": [],
            }
        )
    )
    live = run_case(
        db,
        case_id="case_f",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=False,
        model=stub,
    )
    assert live["parse_status"] == "ok"
    assert live["model"] == "stub"
    assert live["analysis"]["observations"]
    known = evidence_ids(live["evidence"])
    parsed = parse_agent_analysis(
        live["raw_response"],
        known_ids=known,
    )
    assert "weekday:sunday" not in known
    assert parsed.unknown_evidence_refs == ("weekday:sunday",)

    v2 = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
        prompt_version="temporal-v2",
    )
    assert v2["prompt_version"] == "temporal-v2"
    assert v2["prompt"]["version"] == "temporal-v2"
    assert "weekly_morning_afternoon" in v2["evidence"]
    week_id = v2["evidence"]["weekly_morning_afternoon"][0]["id"]
    assert week_id in evidence_ids(v2["evidence"])
    assert "temporal-v2" in v2["prompt"]["system"]
    _assert_no_model_input_leak(v2["prompt"]["system"])
    _assert_no_model_input_leak(v2["prompt"]["user"])

    v3 = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
        prompt_version="temporal-v3",
    )
    assert v3["prompt_version"] == "temporal-v3"
    assert v3["prompt"]["version"] == "temporal-v3"
    assert v3["evidence"] == v2["evidence"]
    assert "temporal-v3" in v3["prompt"]["system"]
    assert v3["prompt"]["system"] != v2["prompt"]["system"]
    _assert_no_model_input_leak(v3["prompt"]["system"])
    _assert_no_model_input_leak(v3["prompt"]["user"])


def test_dependencies_v1_can_be_selected():
    from evaluation.agent.prompt import PROMPT_VERSIONS

    assert PROMPT_VERSION_DEPENDENCIES_V1 == "dependencies-v1"
    assert PROMPT_VERSION_DEPENDENCIES_V1 in PROMPT_VERSIONS
    prompt = render_prompt(
        {"case_id": "case_b"},
        version="dependencies-v1",
    )
    assert prompt.version == "dependencies-v1"
    assert prompt.system == SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1
    assert prompt.system is not SYSTEM_INSTRUCTIONS_V3


def test_require_compatible_routes_dataset_b_to_case_b():
    from evaluation.agent.contracts import require_compatible

    assert require_compatible(
        "interruptions_dependencies",
        "dependencies-v1",
    ) == "case_b"
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "interruptions_dependencies",
            "temporal-v3",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "temporal_patterns",
            "dependencies-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "noise_control",
            "dependencies-v1",
        )
    assert require_compatible(
        "task_age_abandonment",
        "task-age-v1",
    ) == "case_c"
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "task_age_abandonment",
            "temporal-v3",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "temporal_patterns",
            "task-age-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "interruptions_dependencies",
            "task-age-v1",
        )
    assert require_compatible(
        "planning_workload",
        "planning-v1",
    ) == "case_d"
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "planning_workload",
            "temporal-v3",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "planning_workload",
            "task-age-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "temporal_patterns",
            "planning-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "task_age_abandonment",
            "planning-v1",
        )
    assert require_compatible(
        "behaviour_change",
        "change-v1",
    ) == "case_e"
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "behaviour_change",
            "temporal-v3",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "behaviour_change",
            "planning-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "temporal_patterns",
            "change-v1",
        )
    with pytest.raises(ValueError, match="not compatible"):
        require_compatible(
            "planning_workload",
            "change-v1",
        )


def test_cli_rejects_incompatible_scenario_and_contract(capsys):
    from evaluation.agent.runner import main as agent_main

    assert agent_main(
        [
            "--scenario",
            "interruptions_dependencies",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err
    assert "dependencies-v1" in err

    assert agent_main(
        [
            "--scenario",
            "temporal_patterns",
            "--prompt-version",
            "dependencies-v1",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err

    assert agent_main(
        [
            "--scenario",
            "interruptions_dependencies",
            "--dry-run",
        ]
    ) == 2

    assert agent_main(
        [
            "--scenario",
            "task_age_abandonment",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err
    assert "task-age-v1" in err

    assert agent_main(
        [
            "--scenario",
            "task_age_abandonment",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err

    assert agent_main(
        [
            "--scenario",
            "planning_workload",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err
    assert "planning-v1" in err

    assert agent_main(
        [
            "--scenario",
            "planning_workload",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err

    assert agent_main(
        [
            "--scenario",
            "behaviour_change",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err
    assert "change-v1" in err

    assert agent_main(
        [
            "--scenario",
            "behaviour_change",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err


def test_cli_incompatible_planning_pair_fails_before_db_mutation(
    monkeypatch,
    capsys,
):
    def boom(*args, **kwargs):
        raise AssertionError("eval DB must not be mutated")

    monkeypatch.setattr(
        "evaluation.agent.runner.reset_eval_schema",
        boom,
    )
    monkeypatch.setattr(
        "evaluation.agent.runner.create_eval_engine",
        boom,
    )
    from evaluation.agent.runner import main as agent_main

    assert agent_main(
        [
            "--scenario",
            "planning_workload",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err


def test_cli_incompatible_change_pair_fails_before_db_mutation(
    monkeypatch,
    capsys,
):
    def boom(*args, **kwargs):
        raise AssertionError("eval DB must not be mutated")

    monkeypatch.setattr(
        "evaluation.agent.runner.reset_eval_schema",
        boom,
    )
    monkeypatch.setattr(
        "evaluation.agent.runner.create_eval_engine",
        boom,
    )
    from evaluation.agent.runner import main as agent_main

    assert agent_main(
        [
            "--scenario",
            "behaviour_change",
            "--prompt-version",
            "temporal-v3",
            "--dry-run",
        ]
    ) == 2
    err = capsys.readouterr().err
    assert "not compatible" in err


def test_task_age_v1_can_be_selected():
    from evaluation.agent.prompt import PROMPT_VERSIONS

    assert PROMPT_VERSION_TASK_AGE_V1 == "task-age-v1"
    assert PROMPT_VERSION_TASK_AGE_V1 in PROMPT_VERSIONS
    prompt = render_prompt(
        {"case_id": "case_c"},
        version="task-age-v1",
    )
    assert prompt.version == "task-age-v1"
    assert prompt.system == SYSTEM_INSTRUCTIONS_TASK_AGE_V1
    assert prompt.system is not SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1


def test_task_age_prompt_defines_metric_and_limits():
    prompt = render_prompt(
        {"case_id": "case_c"},
        version="task-age-v1",
    )
    text = prompt.system.lower()
    assert "this analysis uses contract task-age-v1" in text
    assert "first non-removed dailytask date" in text
    assert "terminal outcome date" in text
    assert "time since first recorded non-removed" in text
    assert "not time since the task record was created" in text
    assert "association is not causation" in text
    assert "does not establish that ageing caused" in text
    assert "avoid deterministic age rules" in text
    assert "tasks older than 31 days will be abandoned" in text
    assert "old tasks are doomed" in text
    assert "reopened" in text
    assert "cannot be assigned an execution age" in text
    assert "not a sample" in text
    assert "task_age_abandonment" not in prompt.system
    assert "created_at" not in prompt.system
    _assert_no_model_input_leak(prompt.combined_text())


def test_task_age_evidence_uses_production_and_reconciles(
    db,
    make_task,
    monkeypatch,
):
    from app.analytics.task_age import (
        EXECUTION_AGE_BUCKETS,
        classify_execution_age_days,
        task_abandonment_by_execution_age,
        terminal_tasks_with_execution_age,
    )
    from evaluation.agent import evidence as evidence_mod

    young = _seed_task_age_terminals(db, make_task)
    from_date = date(2026, 1, 12)
    to_date = date(2026, 2, 20)
    calls: list[str] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            assert "created_at" not in kwargs
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "task_abandonment_by_execution_age",
        wrap(
            "abandonment",
            evidence_mod.task_abandonment_by_execution_age,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "terminal_tasks_with_execution_age",
        wrap(
            "terminals",
            evidence_mod.terminal_tasks_with_execution_age,
        ),
    )
    evidence = build_task_age_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_c",
    )
    assert calls == ["abandonment", "terminals"]
    production = task_abandonment_by_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    details = terminal_tasks_with_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    assert evidence["case_id"] == "case_c"
    assert set(evidence) == {
        "case_id",
        "period",
        "execution_age_abandonment",
        "terminal_task_drilldown",
    }
    assert evidence["period"]["total_terminal_tasks"] == (
        production.total_terminal_tasks
    )
    assert [
        row["bucket"] for row in evidence["execution_age_abandonment"]
    ] == [group.age_bucket for group in production.groups]
    assert {
        row["bucket"] for row in evidence["execution_age_abandonment"]
    }.issubset({spec.name for spec in EXECUTION_AGE_BUCKETS})
    by_bucket = {
        group.age_bucket: group for group in production.groups
    }
    for row in evidence["execution_age_abandonment"]:
        group = by_bucket[row["bucket"]]
        assert row["id"] == f"execution_age:{row['bucket']}"
        assert row["n"] == group.terminal_task_count
        assert row["completed_count"] == group.completed_count
        assert row["abandoned_count"] == group.abandoned_count
        assert row["abandonment_rate"] == group.abandonment_rate
        assert (
            row["completed_count"] + row["abandoned_count"]
            == row["n"]
        )
        assert "young" not in row
        assert "old" not in row
        assert "trend" not in row
        assert "monotonic" not in row
        assert "risk" not in row
        assert "correlation" not in row
        assert "significance" not in row
    drilldown = evidence["terminal_task_drilldown"]
    assert drilldown["id"] == "terminal_task_drilldown"
    assert drilldown["returned_task_count"] == len(details)
    assert drilldown["returned_task_count"] == (
        production.total_terminal_tasks
    )
    assert "limit" not in drilldown
    assert [row["task_id"] for row in drilldown["tasks"]] == [
        row.task_id for row in details
    ]
    for row, produced in zip(drilldown["tasks"], details):
        assert row["id"] == f"terminal_task:{row['task_id']}"
        assert row["execution_age_days"] == produced.execution_age_days
        assert row["execution_age_bucket"] == classify_execution_age_days(
            produced.execution_age_days
        )
        assert row["terminal_outcome"] == produced.terminal_outcome
        assert "title" not in row
        assert "created_at" not in row
        assert "first_today_date" not in row
    young_row = next(
        row
        for row in drilldown["tasks"]
        if row["task_id"] == young.id
    )
    assert young_row["execution_age_days"] == 0
    blob = serialize_evidence(evidence)
    assert "created_at" not in blob
    assert "task_age_abandonment" not in blob
    _assert_no_model_input_leak(blob)
    source = inspect.getsource(build_task_age_evidence)
    assert "task_abandonment_by_execution_age" in source
    assert "terminal_tasks_with_execution_age" in source
    assert "created_at" not in source
    dispatched = build_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_c",
        version="task-age-v1",
    )
    assert dispatched == evidence
    known = evidence_ids(evidence)
    assert "execution_age:0-2" in known
    assert "terminal_task_drilldown" in known
    assert f"terminal_task:{young.id}" in known
    with pytest.raises(ValueError, match="opaque"):
        build_task_age_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="task_age_abandonment",
        )
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="case_c",
        )


def test_run_case_task_age_dry_run_does_not_call_model(
    db,
    make_task,
    monkeypatch,
):
    _seed_task_age_terminals(db, make_task)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("live model must not be called")

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fail_urlopen,
    )
    with pytest.raises(ValueError, match="not compatible"):
        run_case(
            db,
            case_id="case_c",
            from_date=date(2026, 1, 12),
            to_date=date(2026, 2, 20),
            dry_run=True,
            model=None,
            prompt_version="temporal-v1",
        )
    record = run_case(
        db,
        case_id="case_c",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 2, 20),
        dry_run=True,
        model=None,
        prompt_version="task-age-v1",
    )
    assert record["parse_status"] == "dry_run"
    assert record["raw_response"] is None
    assert record["case_id"] == "case_c"
    assert record["prompt_version"] == "task-age-v1"
    assert "ground_truth" not in record
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    assert "created_at" not in persisted
    _assert_no_model_input_leak(persisted)


def test_planning_v1_can_be_selected():
    from evaluation.agent.prompt import PROMPT_VERSIONS

    assert PROMPT_VERSION_PLANNING_V1 == "planning-v1"
    assert PROMPT_VERSION_PLANNING_V1 in PROMPT_VERSIONS
    prompt = render_prompt(
        {"case_id": "case_d"},
        version="planning-v1",
    )
    assert prompt.version == "planning-v1"
    assert prompt.system == SYSTEM_INSTRUCTIONS_PLANNING_V1
    assert prompt.system is not SYSTEM_INSTRUCTIONS_TASK_AGE_V1


def test_planning_prompt_keeps_planning_concepts_distinct():
    prompt = render_prompt(
        {"case_id": "case_d"},
        version="planning-v1",
    )
    text = prompt.system.lower()
    assert "this analysis uses contract planning-v1" in text
    assert "daily planning versus task effort estimation" in text
    assert "different analytical questions" in text
    assert "unfinished work" in text
    assert "early completion" in text
    assert "abandonment" in text
    assert "unused capacity is not one phenomenon" in text
    assert "systematically overplans" in text
    assert "do not apply a fixed threshold that defines" in text
    assert "planned sessions are the relevant workload" in text
    assert "more dailytasks necessarily represents more planned" in text
    assert "avoid cherry-picking weeks" in text
    assert "one or two extreme weeks" in text
    assert "do not assume that more work causes worse" in text
    assert "planning_workload" not in prompt.system
    assert "heavy week" not in text
    assert "normal week" not in text
    _assert_no_model_input_leak(prompt.combined_text())


def test_planning_evidence_uses_production_and_reconciles(
    db,
    make_task,
    monkeypatch,
):
    from app.analytics.planning import (
        daily_planning_summary,
        task_effort_estimation,
        weekly_workload,
    )
    from evaluation.agent import evidence as evidence_mod

    _seed_planning_case(db, make_task)
    from_date = date(2026, 3, 2)
    to_date = date(2026, 3, 13)
    calls: list[str] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "daily_planning_summary",
        wrap("planning", evidence_mod.daily_planning_summary),
    )
    monkeypatch.setattr(
        evidence_mod,
        "task_effort_estimation",
        wrap("effort", evidence_mod.task_effort_estimation),
    )
    monkeypatch.setattr(
        evidence_mod,
        "weekly_workload",
        wrap("weeks", evidence_mod.weekly_workload),
    )
    evidence = build_planning_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_d",
    )
    assert calls == ["planning", "effort", "weeks"]
    production = daily_planning_summary(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    effort = task_effort_estimation(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    weeks = weekly_workload(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    assert evidence["case_id"] == "case_d"
    assert set(evidence) == {
        "case_id",
        "period",
        "daily_planning",
        "task_effort",
        "weekly_workload",
    }
    planning = evidence["daily_planning"]
    assert planning["id"] == "daily_planning"
    assert planning["total_planned_sessions"] == (
        production.total_planned_sessions
    )
    assert planning["total_actual_sessions"] == (
        production.total_actual_sessions
    )
    assert planning["execution_ratio"] == production.execution_ratio
    assert planning["total_unused_planned_sessions"] == (
        production.total_unused_planned_sessions
    )
    assert planning["unused_while_unfinished"] == (
        production.unused_while_unfinished
    )
    assert planning["unused_due_to_early_completion"] == (
        production.unused_due_to_early_completion
    )
    assert planning["unused_on_abandonment"] == (
        production.unused_on_abandonment
    )
    assert (
        planning["unused_while_unfinished"]
        + planning["unused_due_to_early_completion"]
        + planning["unused_on_abandonment"]
        == planning["total_unused_planned_sessions"]
    )
    assert planning["daily_tasks_without_explicit_plan"] == (
        production.daily_tasks_without_explicit_plan
    )
    assert planning["daily_tasks_without_explicit_plan"] >= 1
    assert "overplanning" not in planning
    assert "overplanned" not in planning
    assert "heavy" not in planning
    assert "underplanning" not in planning
    task_effort = evidence["task_effort"]
    assert task_effort["id"] == "task_effort"
    assert task_effort["completed_tasks_with_estimate"] == (
        effort.completed_tasks_with_estimate
    )
    assert task_effort["mean_estimated_sessions"] == (
        effort.mean_estimated_sessions
    )
    assert task_effort["mean_actual_sessions"] == (
        effort.mean_actual_sessions
    )
    assert task_effort["mean_actual_to_estimated_ratio"] == (
        effort.mean_actual_to_estimated_ratio
    )
    assert task_effort["median_actual_to_estimated_ratio"] == (
        effort.median_actual_to_estimated_ratio
    )
    assert task_effort["below_estimate_count"] == (
        effort.below_estimate_count
    )
    assert task_effort["near_estimate_count"] == effort.near_estimate_count
    assert task_effort["above_estimate_count"] == (
        effort.above_estimate_count
    )
    assert "bias" not in task_effort
    assert "underestimated" not in task_effort
    assert len(evidence["weekly_workload"]) == len(weeks.groups)
    for row, group in zip(evidence["weekly_workload"], weeks.groups):
        week_start = group.week_start_date.isoformat()
        assert row["id"] == f"week:{week_start}"
        assert row["week_start"] == week_start
        assert row["planned_sessions"] == group.planned_sessions
        assert row["daily_task_count"] == group.daily_task_count
        assert row["actual_sessions"] == group.actual_sessions
        assert row["positive_sessions"] == group.positive_sessions
        assert row["negative_sessions"] == group.negative_sessions
        assert row["positive_rate"] == group.positive_rate
        assert "heavy" not in row
        assert "normal" not in row
        assert "percentile" not in row
        assert "threshold" not in row
    blob = serialize_evidence(evidence)
    assert "overplanning" not in blob
    assert "heavy_week" not in blob
    assert "planning_workload" not in blob
    source = inspect.getsource(build_planning_evidence)
    assert "daily_planning_summary" in source
    assert "task_effort_estimation" in source
    assert "weekly_workload" in source
    assert "overplanning" not in source
    assert "heavy_week" not in source
    dispatched = build_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_d",
        version="planning-v1",
    )
    assert dispatched == evidence
    known = evidence_ids(evidence)
    assert "daily_planning" in known
    assert "task_effort" in known
    assert f"week:{weeks.groups[0].week_start_date.isoformat()}" in known
    _assert_no_model_input_leak(blob)
    with pytest.raises(ValueError, match="opaque"):
        build_planning_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="planning_workload",
        )
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="case_d",
        )


def test_run_case_planning_dry_run_does_not_call_model(
    db,
    make_task,
    monkeypatch,
):
    _seed_planning_case(db, make_task)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("live model must not be called")

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fail_urlopen,
    )
    with pytest.raises(ValueError, match="not compatible"):
        run_case(
            db,
            case_id="case_d",
            from_date=date(2026, 3, 2),
            to_date=date(2026, 3, 13),
            dry_run=True,
            model=None,
            prompt_version="temporal-v1",
        )
    record = run_case(
        db,
        case_id="case_d",
        from_date=date(2026, 3, 2),
        to_date=date(2026, 3, 13),
        dry_run=True,
        model=None,
        prompt_version="planning-v1",
    )
    assert record["parse_status"] == "dry_run"
    assert record["raw_response"] is None
    assert record["case_id"] == "case_d"
    assert record["prompt_version"] == "planning-v1"
    assert "ground_truth" not in record
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    assert "planning_workload" not in persisted
    assert "heavy_week" not in persisted
    _assert_no_model_input_leak(persisted)


def test_change_v1_can_be_selected():
    from evaluation.agent.prompt import PROMPT_VERSIONS

    assert PROMPT_VERSION_CHANGE_V1 == "change-v1"
    assert PROMPT_VERSION_CHANGE_V1 in PROMPT_VERSIONS
    prompt = render_prompt(
        {"case_id": "case_e"},
        version="change-v1",
    )
    assert prompt.version == "change-v1"
    assert prompt.system == SYSTEM_INSTRUCTIONS_CHANGE_V1
    assert prompt.system is not SYSTEM_INSTRUCTIONS_PLANNING_V1


def test_change_prompt_distinguishes_history_change_and_noise():
    prompt = render_prompt(
        {"case_id": "case_e"},
        version="change-v1",
    )
    text = prompt.system.lower()
    assert "this analysis uses contract change-v1" in text
    assert "full historical period" in text
    assert "may no longer describe recent behaviour" in text
    assert "do not automatically privilege lifetime aggregates" in text
    assert "one dramatic week or one small recent window" in text
    assert "do not require every recent week to agree" in text
    assert "previously strong relationship is no longer" in text
    assert "do not need to invent a new reversed relationship" in text
    assert "do not infer a new behavioural regime from one week" in text
    assert "does not establish why they changed" in text
    assert "do not infer a cause from the timing" in text
    assert "behaviour_change" not in prompt.system
    assert "decoy" not in text
    assert "historical_phase" not in text
    assert "transition_phase" not in text
    _assert_no_model_input_leak(prompt.combined_text())


def test_change_evidence_uses_production_and_reconciles(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    from datetime import timedelta

    from app.analytics.change import (
        compare_morning_afternoon_windows,
        morning_afternoon_window,
        rolling_window_dates,
        weekly_morning_afternoon_outcomes,
    )
    from evaluation.agent import evidence as evidence_mod

    _seed_change_sessions(db, make_task, make_daily_task)
    from_date = date(2026, 1, 12)
    to_date = date(2026, 4, 24)
    calls: list[str] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "morning_afternoon_window",
        wrap("window", evidence_mod.morning_afternoon_window),
    )
    monkeypatch.setattr(
        evidence_mod,
        "weekly_morning_afternoon_outcomes",
        wrap("weeks", evidence_mod.weekly_morning_afternoon_outcomes),
    )
    monkeypatch.setattr(
        evidence_mod,
        "compare_morning_afternoon_windows",
        wrap("compare", evidence_mod.compare_morning_afternoon_windows),
    )
    monkeypatch.setattr(
        evidence_mod,
        "rolling_window_dates",
        wrap("rolling", evidence_mod.rolling_window_dates),
    )
    evidence = build_change_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_e",
    )
    assert calls.count("rolling") == 3
    assert calls.count("window") == 4
    assert "compare" in calls
    assert "weeks" in calls
    recent8_from, recent8_to = rolling_window_dates(to_date, weeks=8)
    recent16_from, recent16_to = rolling_window_dates(
        to_date,
        weeks=16,
    )
    preceding16_from, preceding16_to = rolling_window_dates(
        recent16_from - timedelta(days=1),
        weeks=16,
    )
    production_full = morning_afternoon_window(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    production_8 = morning_afternoon_window(
        db,
        from_date=recent8_from,
        to_date=recent8_to,
    )
    production_16 = morning_afternoon_window(
        db,
        from_date=recent16_from,
        to_date=recent16_to,
    )
    production_prec = morning_afternoon_window(
        db,
        from_date=preceding16_from,
        to_date=preceding16_to,
    )
    compared = compare_morning_afternoon_windows(
        db,
        current_from_date=recent16_from,
        current_to_date=recent16_to,
        baseline_from_date=preceding16_from,
        baseline_to_date=preceding16_to,
    )
    weeks = weekly_morning_afternoon_outcomes(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    assert evidence["case_id"] == "case_e"
    assert set(evidence) == {
        "case_id",
        "period",
        "full_period",
        "recent_windows",
        "preceding_windows",
        "window_comparisons",
        "weekly_morning_afternoon",
    }
    full = evidence["full_period"]
    assert full["id"] == "window:full"
    assert full["from"] == production_full.from_date.isoformat()
    assert full["to"] == production_full.to_date.isoformat()
    assert full["morning_n"] == production_full.morning_session_count
    assert full["morning_positive_rate"] == (
        production_full.morning_positive_rate
    )
    assert full["afternoon_n"] == (
        production_full.afternoon_session_count
    )
    assert full["afternoon_positive_rate"] == (
        production_full.afternoon_positive_rate
    )
    assert full["positive_rate_gap"] == (
        production_full.positive_rate_gap
    )
    by_id = {
        row["id"]: row
        for row in (
            *evidence["recent_windows"],
            *evidence["preceding_windows"],
        )
    }
    assert by_id["window:recent-8w"]["from"] == recent8_from.isoformat()
    assert by_id["window:recent-8w"]["to"] == recent8_to.isoformat()
    assert by_id["window:recent-8w"]["morning_n"] == (
        production_8.morning_session_count
    )
    assert by_id["window:recent-8w"]["positive_rate_gap"] == (
        production_8.positive_rate_gap
    )
    assert by_id["window:recent-16w"]["from"] == (
        recent16_from.isoformat()
    )
    assert by_id["window:recent-16w"]["morning_n"] == (
        production_16.morning_session_count
    )
    assert by_id["window:preceding-16w"]["from"] == (
        preceding16_from.isoformat()
    )
    assert by_id["window:preceding-16w"]["to"] == (
        preceding16_to.isoformat()
    )
    assert by_id["window:preceding-16w"]["positive_rate_gap"] == (
        production_prec.positive_rate_gap
    )
    comparison = evidence["window_comparisons"][0]
    assert comparison["id"] == "comparison:recent16-vs-preceding16"
    assert comparison["current_id"] == "window:recent-16w"
    assert comparison["baseline_id"] == "window:preceding-16w"
    assert comparison["gap_change"] == compared.gap_change
    assert comparison["morning_rate_change"] == (
        compared.morning_rate_change
    )
    assert comparison["afternoon_rate_change"] == (
        compared.afternoon_rate_change
    )
    assert [
        row["week_start"] for row in evidence["weekly_morning_afternoon"]
    ] == [group.week_start_date.isoformat() for group in weeks.groups]
    for row, group in zip(
        evidence["weekly_morning_afternoon"],
        weeks.groups,
    ):
        assert row["id"] == f"week:{row['week_start']}"
        assert row["morning_n"] == group.morning_session_count
        assert row["morning_positive_rate"] == group.morning_positive_rate
        assert row["afternoon_n"] == group.afternoon_session_count
        assert row["afternoon_positive_rate"] == (
            group.afternoon_positive_rate
        )
        assert row["positive_rate_gap"] == group.positive_rate_gap
        assert "decoy" not in row
        assert "phase" not in row
        assert "regime" not in row
        assert "trend" not in row
    blob = serialize_evidence(evidence)
    for token in (
        "historical_phase",
        "transition_phase",
        "recent_phase",
        "decoy",
        "change_point",
        "behaviour_change",
    ):
        assert token not in blob
    source = inspect.getsource(build_change_evidence)
    assert "morning_afternoon_window" in source
    assert "weekly_morning_afternoon_outcomes" in source
    assert "compare_morning_afternoon_windows" in source
    assert "rolling_window_dates" in source
    dispatched = build_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_e",
        version="change-v1",
    )
    assert dispatched == evidence
    known = evidence_ids(evidence)
    assert "window:full" in known
    assert "window:recent-8w" in known
    assert "window:recent-16w" in known
    assert "window:preceding-16w" in known
    assert "comparison:recent16-vs-preceding16" in known
    assert f"week:{weeks.groups[0].week_start_date.isoformat()}" in known
    _assert_no_model_input_leak(blob)
    with pytest.raises(ValueError, match="opaque"):
        build_change_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="behaviour_change",
        )
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="case_e",
        )


def test_run_case_change_dry_run_does_not_call_model(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    _seed_change_sessions(db, make_task, make_daily_task)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("live model must not be called")

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fail_urlopen,
    )
    with pytest.raises(ValueError, match="not compatible"):
        run_case(
            db,
            case_id="case_e",
            from_date=date(2026, 1, 12),
            to_date=date(2026, 4, 24),
            dry_run=True,
            model=None,
            prompt_version="temporal-v1",
        )
    record = run_case(
        db,
        case_id="case_e",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 4, 24),
        dry_run=True,
        model=None,
        prompt_version="change-v1",
    )
    assert record["parse_status"] == "dry_run"
    assert record["raw_response"] is None
    assert record["case_id"] == "case_e"
    assert record["prompt_version"] == "change-v1"
    assert "ground_truth" not in record
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    assert "behaviour_change" not in persisted
    assert "decoy" not in persisted.lower()
    _assert_no_model_input_leak(persisted)


def test_dependencies_evidence_uses_production_analytics(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    from app.analytics.drilldown import DEFAULT_LIMIT
    from evaluation.agent import evidence as evidence_mod

    waiting, notes = _seed_dependency_sessions(
        db,
        make_task,
        make_daily_task,
    )
    calls: list[str] = []
    limits: list[int] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            if "limit" in kwargs:
                limits.append(kwargs["limit"])
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_interruption",
        wrap(
            "interruptions",
            evidence_mod.session_outcomes_by_interruption,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "stuck_task_drilldown",
        wrap(
            "drilldown",
            evidence_mod.stuck_task_drilldown,
        ),
    )
    evidence = build_dependencies_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_b",
    )
    assert calls == ["interruptions", "drilldown"]
    assert limits == [DEFAULT_LIMIT]
    dispatched = build_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_b",
        version="dependencies-v1",
    )
    assert dispatched == evidence
    source = inspect.getsource(build_dependencies_evidence)
    assert "session_outcomes_by_interruption" in source
    assert "stuck_task_drilldown" in source
    assert "parse_task_category" not in source
    assert "positive_rate =" not in source
    assert waiting.title in serialize_evidence(evidence)
    assert notes.title in serialize_evidence(evidence)


def test_dependencies_evidence_is_opaque_and_reconciles(
    db,
    make_task,
    make_daily_task,
):
    from app.analytics.drilldown import DEFAULT_LIMIT
    from app.analytics.drilldown import stuck_task_drilldown
    from app.analytics.outcomes import session_outcomes_by_interruption

    waiting, notes = _seed_dependency_sessions(
        db,
        make_task,
        make_daily_task,
    )
    from_date = date(2026, 1, 12)
    to_date = date(2026, 1, 16)
    production_int = session_outcomes_by_interruption(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    production_stuck = stuck_task_drilldown(
        db,
        from_date=from_date,
        to_date=to_date,
        limit=DEFAULT_LIMIT,
    )
    evidence = build_dependencies_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_b",
    )
    assert evidence["case_id"] == "case_b"
    assert set(evidence) == {
        "case_id",
        "period",
        "interruption_outcomes",
        "stuck_drilldown",
    }
    flags = {
        row["interrupted"] for row in evidence["interruption_outcomes"]
    }
    assert flags == {False, True}
    ids = {row["id"] for row in evidence["interruption_outcomes"]}
    assert ids == {"interruption:false", "interruption:true"}
    by_flag = {
        group.interrupted: group for group in production_int.groups
    }
    for row in evidence["interruption_outcomes"]:
        group = by_flag[row["interrupted"]]
        assert row["n"] == group.session_count
        assert row["progress"] == group.progress_count
        assert row["complete"] == group.complete_count
        assert row["stuck"] == group.stuck_count
        assert row["paused"] == group.paused_count
        assert row["abandoned"] == group.abandoned_count
        assert row["positive_count"] == group.positive_count
        assert row["negative_count"] == group.negative_count
        assert row["positive_rate"] == group.positive_rate
        assert (
            row["progress"] + row["complete"]
            == row["positive_count"]
        )
        assert (
            row["stuck"] + row["paused"] + row["abandoned"]
            == row["negative_count"]
        )
        assert (
            row["positive_count"] + row["negative_count"]
            == row["n"]
        )
        assert "category" not in row
        assert "cluster" not in row
        assert "effect" not in row
    drilldown = evidence["stuck_drilldown"]
    assert drilldown["id"] == "stuck_drilldown"
    assert drilldown["limit"] == DEFAULT_LIMIT
    assert drilldown["total_stuck_sessions"] == (
        production_stuck.total_stuck_sessions
    )
    assert drilldown["total_distinct_stuck_tasks"] == (
        production_stuck.total_distinct_stuck_tasks
    )
    assert drilldown["returned_task_count"] == (
        production_stuck.returned_task_count
    )
    assert drilldown["returned_task_count"] == len(drilldown["tasks"])
    assert drilldown["returned_task_count"] <= drilldown["limit"]
    production_by_id = {
        row.task_id: row for row in production_stuck.tasks
    }
    for row in drilldown["tasks"]:
        produced = production_by_id[row["task_id"]]
        assert row["id"] == f"stuck_task:{row['task_id']}"
        assert row["title"] == produced.title
        assert row["stuck_session_count"] == (
            produced.stuck_session_count
        )
        assert row["first_stuck_date"] == (
            produced.first_stuck_date.isoformat()
        )
        assert row["last_stuck_date"] == (
            produced.last_stuck_date.isoformat()
        )
        assert row["task_status"] == produced.task_status
        assert "category" not in row
        assert "cluster" not in row
        assert "hr_related" not in row
    titles = {row["title"] for row in drilldown["tasks"]}
    assert waiting.title in titles
    assert notes.title in titles
    known = evidence_ids(evidence)
    assert "interruption:false" in known
    assert "interruption:true" in known
    assert "stuck_drilldown" in known
    assert f"stuck_task:{waiting.id}" in known
    blob = serialize_evidence(evidence)
    _assert_no_model_input_leak(blob)
    assert "weekday_outcomes" not in evidence
    with pytest.raises(ValueError, match="opaque"):
        build_dependencies_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="interruptions_dependencies",
        )
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id="case_b",
        )


def test_dependencies_prompt_explains_drilldown_and_causation():
    prompt = render_prompt(
        {"case_id": "case_b"},
        version="dependencies-v1",
    )
    text = prompt.system.lower()
    assert "this analysis uses contract dependencies-v1" in text
    assert "frequency- and recency-biased" in text
    assert "not a random sample" in text
    assert "not a representative sample" in text
    assert "repeated or recent stuck" in text
    assert "total_stuck_sessions" in text
    assert "total_distinct_stuck_tasks" in text
    assert "returned_task_count" in text
    assert "association is not causation" in text
    assert "does not establish that interruptions caused" in text
    assert "do not combine separate observations" in text
    assert "two separate observed relationships" in text
    assert "does not establish whether they are related" in text
    assert "tentative semantic hypothesis" in text
    assert "not a claim of a verified task category" in text
    assert "denominator-based category statistics" in text
    assert "arbitrary sql" in text
    assert "patterns to be empty" in text
    assert "interruptions_dependencies" not in prompt.system
    assert "dataset b" not in text
    _assert_no_model_input_leak(prompt.combined_text())


def test_run_case_dependencies_dry_run_does_not_call_model(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    waiting, _notes = _seed_dependency_sessions(
        db,
        make_task,
        make_daily_task,
    )

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("live model must not be called")

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fail_urlopen,
    )
    with pytest.raises(ValueError, match="not compatible"):
        run_case(
            db,
            case_id="case_b",
            from_date=date(2026, 1, 12),
            to_date=date(2026, 1, 16),
            dry_run=True,
            model=None,
            prompt_version="temporal-v1",
        )
    record = run_case(
        db,
        case_id="case_b",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
        prompt_version="dependencies-v1",
    )
    assert record["parse_status"] == "dry_run"
    assert record["raw_response"] is None
    assert record["case_id"] == "case_b"
    assert record["prompt_version"] == "dependencies-v1"
    assert "ground_truth" not in record
    titles = [
        row["title"]
        for row in record["evidence"]["stuck_drilldown"]["tasks"]
    ]
    assert waiting.title in titles
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    _assert_no_model_input_leak(persisted)
    stub = StubAnalysisModel(
        json.dumps(
            {
                "observations": [
                    {
                        "statement": "Interrupted sessions differ.",
                        "evidence_refs": ["interruption:true"],
                    }
                ],
                "patterns": [],
                "hypotheses": [],
                "insufficient_evidence": [
                    {
                        "statement": "Unknown task cited.",
                        "evidence_refs": ["stuck_task:0"],
                    }
                ],
                "suggested_drilldowns": [],
            }
        )
    )
    live = run_case(
        db,
        case_id="case_b",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=False,
        model=stub,
        prompt_version="dependencies-v1",
    )
    assert live["parse_status"] == "ok"
    known = evidence_ids(live["evidence"])
    parsed = parse_agent_analysis(
        live["raw_response"],
        known_ids=known,
    )
    assert "stuck_task:0" not in known
    assert parsed.unknown_evidence_refs == ("stuck_task:0",)


def test_cli_refuses_development_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://system1:system1@db:5432/system1",
    )
    from evaluation.agent.runner import main as agent_main

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        agent_main(
            ["--scenario", "temporal_patterns", "--dry-run"]
        )


SECRET_KEY = "sk-test-secret-live-key"
PROMPT_MARKER = "unique-prompt-body-should-not-appear"


def _provider_prompt() -> ModelPrompt:
    return ModelPrompt(
        version=PROMPT_VERSION,
        system="system-instructions",
        user=PROMPT_MARKER,
    )


def _configured_model() -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        api_key=SECRET_KEY,
        base_url="https://example.invalid/v1",
        model="example-model",
    )


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        status,
        "Error",
        {"Content-Type": "application/json"},
        BytesIO(body),
    )


def _stub_http_error(monkeypatch, status: int, body: bytes) -> None:
    def fake_urlopen(request, timeout=None):
        raise _http_error(status, body)

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fake_urlopen,
    )


class _FakeCompletionsResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {"message": {"content": "{}"}}
                ]
            }
        ).encode("utf-8")


def test_openai_compatible_model_omits_temperature(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeCompletionsResponse()

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fake_urlopen,
    )
    result = _configured_model().analyse(_provider_prompt())
    assert result.model_identifier == "example-model"
    assert captured["url"].endswith("/chat/completions")
    assert "temperature" not in captured["body"]
    assert captured["body"]["model"] == "example-model"
    assert captured["body"]["response_format"] == {
        "type": "json_object"
    }
    assert captured["body"]["messages"][0]["content"] == (
        "system-instructions"
    )
    assert captured["body"]["messages"][1]["content"] == (
        PROMPT_MARKER
    )


def test_openai_compatible_model_surfaces_http_400_fields(
    monkeypatch,
):
    payload = {
        "error": {
            "message": (
                "Unsupported parameter: 'response_format'."
            ),
            "type": "invalid_request_error",
            "param": "response_format",
            "code": "unsupported_parameter",
        }
    }
    _stub_http_error(
        monkeypatch,
        400,
        json.dumps(payload).encode("utf-8"),
    )
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message == (
        "Model HTTP 400: unsupported_parameter — "
        '"Unsupported parameter: \'response_format\'." '
        "(type: invalid_request_error; param: response_format)"
    )
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message


def test_openai_compatible_model_surfaces_http_429_fields(
    monkeypatch,
):
    payload = {
        "error": {
            "message": "Rate limit reached for requests.",
            "type": "rate_limit_error",
            "param": None,
            "code": "rate_limit_exceeded",
        }
    }
    _stub_http_error(
        monkeypatch,
        429,
        json.dumps(payload).encode("utf-8"),
    )
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message == (
        "Model HTTP 429: rate_limit_exceeded — "
        '"Rate limit reached for requests." '
        "(type: rate_limit_error)"
    )
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message


def test_openai_compatible_model_truncates_non_json_http_error(
    monkeypatch,
):
    body = (
        SECRET_KEY.encode("utf-8")
        + b" <html>"
        + (b"rate-limit-page " * 80)
        + b"</html>"
    )
    _stub_http_error(monkeypatch, 400, body)
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message.startswith("Model HTTP 400: ")
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message
    assert len(message) <= len("Model HTTP 400: ") + ERROR_BODY_LIMIT + 3

