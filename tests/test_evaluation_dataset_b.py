import inspect
import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.drilldown import stuck_task_drilldown
from app.analytics.outcomes import (
    session_outcomes_by_interruption,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import parse_task_category
from evaluation.clock import classify_day_part, working_days
from evaluation.generate import main
from evaluation.observe import STUCK_TITLE_SAMPLE_LIMIT, bounded_stuck_titles
from evaluation.scenarios import interruptions_dependencies as scenario
from evaluation.scenarios.interruptions_dependencies import (
    DEFAULT_SEED,
    START_DATE,
    WORKING_WEEKS,
    generate_interruptions_dependencies,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "interruptions_dependencies.yaml"
)
HIGH_VOLUME_DAY_PARTS = (
    "morning",
    "early_afternoon",
    "late_afternoon",
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_interruptions_dependencies,
        DEFAULT_SEED,
    )


@pytest.fixture
def eval_db(generated_eval):
    session = generated_eval["SessionLocal"]()
    try:
        yield session
    finally:
        session.close()


def _local_zone():
    from zoneinfo import ZoneInfo

    return ZoneInfo(get_settings().timezone)


def _session_rows(eval_db: Session):
    zone = _local_zone()
    rows = []
    query = (
        eval_db.query(WorkSession, DailyTask, Task)
        .join(DailyTask, WorkSession.daily_task_id == DailyTask.id)
        .join(Task, DailyTask.task_id == Task.id)
    )
    for work, daily, task in query:
        local = work.started_at.astimezone(zone)
        rows.append(
            {
                "work": work,
                "daily": daily,
                "task": task,
                "local": local,
                "day_part": classify_day_part(local),
                "weekday": local.weekday(),
                "positive": work.outcome in POSITIVE,
                "stuck": work.outcome == "stuck",
                "category": parse_task_category(task.title),
                "task_age_days": (
                    daily.date
                    - task.created_at.astimezone(zone).date()
                ).days,
            }
        )
    return rows


def _rate(rows, predicate, key="positive") -> float:
    matched = [row for row in rows if predicate(row)]
    assert matched
    return sum(row[key] for row in matched) / len(matched)


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "interruptions_dependencies"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert payload["agent_evidence_contract"] == "dependencies-v1"
    generator_truth = payload["generator_truth"]
    assert "interrupted_sessions_have_lower_positive_outcome_rate" in (
        generator_truth
    )
    assert "hr_tasks_have_materially_higher_stuck_rate" in (
        generator_truth
    )
    assert "hr_interruption_rate_is_not_materially_higher_than_non_hr" in (
        generator_truth
    )
    assert "expected_patterns" not in payload


def test_agent_evaluable_ground_truth_matches_evidence_contract():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())
    agent_patterns = payload["agent_expected_patterns"]
    agent_hypotheses = payload["agent_expected_hypotheses"]
    agent_insufficient = payload["agent_expected_insufficient_evidence"]
    able_to_say = payload["agent_should_be_able_to_say"]
    must_not = payload["agent_should_not_claim"]
    hidden_from_agent = set(payload["not_agent_evaluable"])
    able_blob = " ".join(able_to_say).lower()
    must_blob = " ".join(must_not).lower()

    assert "interrupted_sessions_have_lower_positive_outcome_rate" in (
        agent_patterns
    )
    assert "repeated_stuck_work_is_concentrated_in_the_bounded_drilldown" in (
        agent_patterns
    )
    assert (
        "displayed_repeatedly_stuck_tasks_share_approval_waiting_response_language"
        in agent_patterns
    )
    assert hidden_from_agent.isdisjoint(agent_patterns)
    assert hidden_from_agent.isdisjoint(agent_hypotheses)
    assert all(
        item not in able_blob
        for item in (
            "hr tasks have a materially higher stuck rate",
            "hr interruption rates are not materially higher",
            "not explained by a higher interruption rate",
        )
    )
    assert (
        "title_language_suggests_some_repeatedly_stuck_work_may_involve_dependencies_or_waiting_on_decisions"
        in agent_hypotheses
    )
    assert "verified task category" in " ".join(must_not).lower()
    assert "interruptions_do_not_have_a_demonstrated_causal_effect" in (
        agent_insufficient
    )
    assert (
        "interruption_status_is_not_linked_to_the_repeated_stuck_cluster_in_supplied_evidence"
        in agent_insufficient
    )
    assert "no_hr_wide_stuck_rate_can_be_inferred" in agent_insufficient
    assert "no_hr_wide_interruption_rate_can_be_inferred" in (
        agent_insufficient
    )
    assert "bounded_top_n_drilldown_is_not_representative_of_all_tasks" in (
        agent_insufficient
    )
    assert "materially poorer observed outcomes" in able_blob
    assert "approval" in able_blob
    assert "tentative hypothesis" in able_blob
    assert "separate observations" in able_blob
    assert "not establish whether interruptions are concentrated" in (
        able_blob
    )
    assert "not representative of all tasks" in able_blob
    assert "no valid hr-wide stuck rate" in able_blob
    assert "no valid hr-wide interruption rate" in able_blob
    assert "interruptions cause poor performance" in must_blob
    assert "specific stuck rate" in must_blob
    assert "displayed 12 tasks represent all hr" in must_blob
    assert "explains the stuck-title cluster" in must_blob
    assert "hr_tasks_have_materially_higher_stuck_rate" in (
        payload["generator_truth"]
    )
    package_text = (
        Path(__file__).resolve().parents[1]
        / "evaluation"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "only be evaluated against conclusions supported" in (
        package_text
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "interruptions_dependencies.yaml" not in generate_source
    assert "interruptions_dependencies.yaml" not in scenario_source
    assert "yaml.safe_load" not in scenario_source
    assert "yaml.safe_load" not in generate_source


def test_same_seed_is_reproducible(generated_eval):
    assert generated_eval["first_snap"] == generated_eval["second_snap"]
    assert (
        generated_eval["first_result"].work_session_count
        == generated_eval["result"].work_session_count
    )


def test_alembic_schema_was_applied(eval_db):
    version = eval_db.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert version == "c3f8e2a1b0d4"


def test_volume_and_date_range_are_sensible(generated_eval, eval_db):
    result = generated_eval["result"]
    days = working_days(START_DATE, WORKING_WEEKS)

    assert result.start_date == days[0]
    assert result.end_date == days[-1]
    assert 650 <= result.work_session_count <= 850
    assert 90 <= result.task_count <= 130
    assert result.daily_task_count < result.work_session_count

    active_days = {
        row[0]
        for row in eval_db.query(DailyTask.date).distinct()
    }
    assert min(active_days) >= days[0]
    assert max(active_days) <= days[-1]
    empty_days = set(days) - active_days
    assert empty_days
    assert len(empty_days) <= 8


def test_no_weekend_daily_tasks_or_sessions(eval_db):
    zone = _local_zone()

    for daily in eval_db.query(DailyTask):
        assert daily.date.weekday() < 5

    for work in eval_db.query(WorkSession):
        local = work.started_at.astimezone(zone)
        assert local.weekday() < 5
        assert work.started_at.tzinfo is not None
        assert work.ended_at is not None
        assert work.ended_at >= work.started_at
        assert work.session_state == "completed"
        assert work.outcome in POSITIVE | NEGATIVE


def test_timestamps_fall_in_intended_range(eval_db):
    zone = _local_zone()
    days = working_days(START_DATE, WORKING_WEEKS)

    for work in eval_db.query(WorkSession):
        local_date = work.started_at.astimezone(zone).date()
        assert days[0] <= local_date <= days[-1]


def test_core_relational_constraints(eval_db):
    task_ids = {task.id for task in eval_db.query(Task)}
    daily_ids = {daily.id for daily in eval_db.query(DailyTask)}
    seen_pairs: set[tuple[int, date]] = set()

    for daily in eval_db.query(DailyTask):
        assert daily.task_id in task_ids
        pair = (daily.task_id, daily.date)
        assert pair not in seen_pairs
        seen_pairs.add(pair)

    for work in eval_db.query(WorkSession):
        assert work.daily_task_id in daily_ids

    for task in eval_db.query(Task):
        if task.parent_task_id is not None:
            assert task.parent_task_id in task_ids
        if task.status == "completed":
            assert task.completed_at is not None
        if task.status == "cancelled":
            assert task.completed_at is None

    complete_sessions = (
        eval_db.query(WorkSession)
        .filter(WorkSession.outcome == "complete")
        .all()
    )
    assert complete_sessions
    for work in complete_sessions:
        daily = eval_db.get(DailyTask, work.daily_task_id)
        task = eval_db.get(Task, daily.task_id)
        assert daily.state == "completed"
        assert task.status == "completed"

    abandoned_sessions = (
        eval_db.query(WorkSession)
        .filter(WorkSession.outcome == "abandoned")
        .all()
    )
    assert abandoned_sessions
    for work in abandoned_sessions:
        daily = eval_db.get(DailyTask, work.daily_task_id)
        task = eval_db.get(Task, daily.task_id)
        assert daily.state == "abandoned"
        assert task.status == "cancelled"

    spanning = len(
        eval_db.query(DailyTask.task_id)
        .group_by(DailyTask.task_id)
        .having(func.count(DailyTask.id) > 1)
        .all()
    )
    assert spanning >= 5

    multi_session_days = len(
        eval_db.query(WorkSession.daily_task_id)
        .group_by(WorkSession.daily_task_id)
        .having(func.count(WorkSession.id) > 1)
        .all()
    )
    assert multi_session_days >= 5


def test_one_running_session_constraint_exists(eval_db):
    daily = eval_db.query(DailyTask).first()
    assert daily is not None

    eval_db.add(
        WorkSession(
            daily_task_id=daily.id,
            session_state="running",
        )
    )
    eval_db.flush()
    eval_db.add(
        WorkSession(
            daily_task_id=daily.id,
            session_state="running",
        )
    )
    with pytest.raises(IntegrityError):
        eval_db.flush()
    eval_db.rollback()


def test_interrupted_sessions_have_worse_outcomes(eval_db):
    """Generator validation: planted interruption/outcome association."""
    rows = _session_rows(eval_db)
    interruption_rate = sum(
        row["work"].interrupted for row in rows
    ) / len(rows)
    assert 0.15 <= interruption_rate <= 0.25

    positive_uninterrupted = _rate(
        rows,
        lambda row: not row["work"].interrupted,
    )
    positive_interrupted = _rate(
        rows,
        lambda row: row["work"].interrupted,
    )

    assert positive_uninterrupted - positive_interrupted >= 0.18
    assert positive_uninterrupted > 0.60
    assert positive_interrupted < 0.55

    interrupted_rows = [
        row for row in rows if row["work"].interrupted
    ]
    uninterrupted_rows = [
        row for row in rows if not row["work"].interrupted
    ]
    assert any(row["positive"] for row in interrupted_rows)
    assert any(not row["positive"] for row in interrupted_rows)
    assert any(row["positive"] for row in uninterrupted_rows)
    assert any(not row["positive"] for row in uninterrupted_rows)


def test_hr_stuck_rate_is_materially_higher(eval_db):
    """Generator truth: planted HR stuck-rate structure.

    This is not an agent-evaluable conclusion under dependencies-v1.
    """
    rows = _session_rows(eval_db)
    hr_stuck = _rate(
        rows,
        lambda row: row["category"] == "HR",
        key="stuck",
    )
    non_hr_stuck = _rate(
        rows,
        lambda row: row["category"] != "HR",
        key="stuck",
    )
    hr_rows = [row for row in rows if row["category"] == "HR"]
    hr_outcomes = {row["work"].outcome for row in hr_rows}

    assert 0.32 <= hr_stuck <= 0.50
    assert 0.06 <= non_hr_stuck <= 0.20
    assert hr_stuck - non_hr_stuck >= 0.18
    assert hr_outcomes & POSITIVE
    assert "stuck" in hr_outcomes
    assert "paused" in hr_outcomes or "abandoned" in hr_outcomes


def test_hr_is_not_more_interrupted(eval_db):
    """Generator truth: planted HR/non-HR interruption neutrality.

    This is not an agent-evaluable conclusion under dependencies-v1.
    """
    rows = _session_rows(eval_db)

    def interruption_rate(predicate) -> float:
        matched = [row for row in rows if predicate(row)]
        assert matched
        return sum(row["work"].interrupted for row in matched) / len(
            matched
        )

    hr = interruption_rate(lambda row: row["category"] == "HR")
    non_hr = interruption_rate(lambda row: row["category"] != "HR")
    assert abs(hr - non_hr) <= 0.07


def test_hr_and_interruptions_are_spread_in_time(eval_db):
    rows = _session_rows(eval_db)
    hr_weekdays = {
        row["weekday"] for row in rows if row["category"] == "HR"
    }
    hr_parts = {
        row["day_part"] for row in rows if row["category"] == "HR"
    }
    interrupted_weekdays = {
        row["weekday"] for row in rows if row["work"].interrupted
    }
    interrupted_parts = {
        row["day_part"] for row in rows if row["work"].interrupted
    }

    assert len(hr_weekdays) >= 4
    assert len(hr_parts) >= 3
    assert len(interrupted_weekdays) >= 4
    assert len(interrupted_parts) >= 3


def test_no_strong_weekday_or_daypart_positive_effect(eval_db):
    rows = _session_rows(eval_db)
    weekday_rates = [
        _rate(rows, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    part_rates = [
        _rate(rows, lambda row, p=part: row["day_part"] == p)
        for part in HIGH_VOLUME_DAY_PARTS
    ]
    monday = weekday_rates[0]
    rest = _rate(rows, lambda row: row["weekday"] != 0)

    assert max(weekday_rates) - min(weekday_rates) <= 0.14
    assert max(part_rates) - min(part_rates) <= 0.14
    assert abs(monday - rest) <= 0.10


def test_no_strong_task_age_abandonment_effect(eval_db):
    rows = _session_rows(eval_db)
    ages = [row["task_age_days"] for row in rows]
    median_age = sorted(ages)[len(ages) // 2]

    def abandoned_rate(predicate) -> float:
        matched = [row for row in rows if predicate(row)]
        assert matched
        return sum(
            row["work"].outcome == "abandoned" for row in matched
        ) / len(matched)

    younger = abandoned_rate(
        lambda row: row["task_age_days"] <= median_age
    )
    older = abandoned_rate(
        lambda row: row["task_age_days"] > median_age
    )
    assert abs(older - younger) <= 0.08


def test_bounded_stuck_title_sample_supports_drill_down(eval_db):
    all_stuck_titles = {
        row["task"].title
        for row in _session_rows(eval_db)
        if row["stuck"]
    }
    sample = bounded_stuck_titles(eval_db)
    sample_categories = {
        parse_task_category(title) for title in sample
    }

    assert sample
    assert len(sample) <= STUCK_TITLE_SAMPLE_LIMIT
    assert len(sample) < len(all_stuck_titles)
    assert "HR" in sample_categories
    assert len(sample_categories) >= 2
    hr_count = sum(
        1 for title in sample if parse_task_category(title) == "HR"
    )
    other_count = len(sample) - hr_count
    assert hr_count >= 3
    assert other_count >= 3
    assert all(title in all_stuck_titles for title in sample)
    waiting_words = (
        "await",
        "approval",
        "confirm",
        "decision",
        "response",
        "sign-off",
    )
    hr_titles = [
        title
        for title in sample
        if parse_task_category(title) == "HR"
    ]
    assert any(
        any(word in title.lower() for word in waiting_words)
        for title in hr_titles
    )


def test_categories_include_hr_and_are_not_only_hr(eval_db):
    rows = _session_rows(eval_db)
    categories = {row["category"] for row in rows}
    assert "HR" in categories
    assert "Engineering" in categories
    assert "Finance" in categories
    assert len(categories) >= 5
    hr_share = sum(row["category"] == "HR" for row in rows) / len(rows)
    assert 0.10 <= hr_share <= 0.40


def test_analytics_exposes_dataset_b_interruption_gap(
    generated_eval,
    eval_db,
):
    result = generated_eval["result"]
    analysis = session_outcomes_by_interruption(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    by_flag = {
        group.interrupted: group
        for group in analysis.groups
    }
    uninterrupted = by_flag[False]
    interrupted = by_flag[True]

    assert analysis.total_sessions == result.work_session_count
    assert (
        uninterrupted.positive_count
        + uninterrupted.negative_count
        == uninterrupted.session_count
    )
    assert (
        interrupted.positive_count
        + interrupted.negative_count
        == interrupted.session_count
    )
    assert (
        uninterrupted.positive_rate
        - interrupted.positive_rate
        >= 0.18
    )
    assert uninterrupted.positive_rate > 0.60
    assert interrupted.positive_rate < 0.55
    forbidden = {
        "effect",
        "correlation",
        "significant",
        "confidence",
        "recommendation",
        "interruption_penalty",
    }
    assert forbidden.isdisjoint(
        analysis.__dataclass_fields__
    )
    assert forbidden.isdisjoint(
        uninterrupted.__dataclass_fields__
    )


def test_analytics_dataset_b_stuck_drilldown_is_bounded(
    generated_eval,
    eval_db,
):
    result = generated_eval["result"]
    sample = stuck_task_drilldown(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    titles = [row.title for row in sample.tasks]
    assert sample.returned_task_count == sample.limit
    assert sample.total_distinct_stuck_tasks > sample.returned_task_count
    assert sample.total_stuck_sessions >= sample.total_distinct_stuck_tasks
    assert any(
        "Await contract amendment" in title for title in titles
    )
    assert any(
        "Await hiring-manager response" in title for title in titles
    )
    assert any(
        "Chase reference response" in title for title in titles
    )
    wider = stuck_task_drilldown(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        limit=50,
    )
    assert any(
        not row.title.startswith("[HR]") for row in wider.tasks
    )
    assert all(
        row.stuck_session_count >= 1 for row in sample.tasks
    )
    forbidden = {
        "category",
        "hr_related",
        "cluster",
        "inferred_topic",
        "waiting_on",
    }
    assert forbidden.isdisjoint(sample.__dataclass_fields__)
    assert forbidden.isdisjoint(sample.tasks[0].__dataclass_fields__)


def test_agent_dependencies_evidence_hides_scenario_and_ground_truth(
    generated_eval,
    eval_db,
):
    from app.analytics.drilldown import DEFAULT_LIMIT
    from evaluation.agent.evidence import (
        build_dependencies_evidence,
        evidence_ids,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt
    from evaluation.agent.runner import run_case

    result = generated_eval["result"]
    production_int = session_outcomes_by_interruption(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    production_stuck = stuck_task_drilldown(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    evidence = build_dependencies_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_b",
    )
    assert evidence["case_id"] == "case_b"
    assert evidence["period"]["from"] == result.start_date.isoformat()
    assert evidence["period"]["to"] == result.end_date.isoformat()
    assert evidence["period"]["total_sessions"] == (
        production_int.total_sessions
    )
    assert evidence["period"]["total_sessions"] == (
        result.work_session_count
    )
    by_flag = {
        group.interrupted: group for group in production_int.groups
    }
    assert set(by_flag) == {False, True}
    for row in evidence["interruption_outcomes"]:
        group = by_flag[row["interrupted"]]
        assert row["n"] == group.session_count
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
        assert row["positive_rate"] == group.positive_rate
    drilldown = evidence["stuck_drilldown"]
    assert drilldown["limit"] == DEFAULT_LIMIT
    assert drilldown["limit"] == production_stuck.limit
    assert drilldown["total_stuck_sessions"] == (
        production_stuck.total_stuck_sessions
    )
    assert drilldown["total_distinct_stuck_tasks"] == (
        production_stuck.total_distinct_stuck_tasks
    )
    assert drilldown["returned_task_count"] == (
        production_stuck.returned_task_count
    )
    assert drilldown["returned_task_count"] == drilldown["limit"]
    assert [
        row["title"] for row in drilldown["tasks"]
    ] == [row.title for row in production_stuck.tasks]
    assert [
        row["task_id"] for row in drilldown["tasks"]
    ] == [row.task_id for row in production_stuck.tasks]
    for row in drilldown["tasks"]:
        assert "category" not in row
        assert "cluster" not in row
        assert "hr_related" not in row
    known = evidence_ids(evidence)
    assert "interruption:false" in known
    assert "interruption:true" in known
    assert "stuck_drilldown" in known
    assert drilldown["tasks"][0]["id"] in known
    blob = serialize_evidence(evidence)
    prompt = render_prompt(
        evidence,
        version="dependencies-v1",
    ).combined_text()
    for token in (
        "interruptions_dependencies",
        "expected_patterns",
        "expected_non_patterns",
        "generator_truth",
        "agent_expected",
        "not_agent_evaluable",
        "hr_tasks_have_materially_higher_stuck_rate",
        "agent_should",
        "ground_truth",
        "dataset b",
    ):
        assert token not in blob.lower()
        assert token not in prompt.lower()
    record = run_case(
        eval_db,
        case_id="case_b",
        from_date=result.start_date,
        to_date=result.end_date,
        dry_run=True,
        model=None,
        prompt_version="dependencies-v1",
    )
    assert record["case_id"] == "case_b"
    assert record["parse_status"] == "dry_run"
    assert record["raw_response"] is None
    assert "ground_truth" not in record
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    for token in (
        "interruptions_dependencies",
        "expected_patterns",
        "generator_truth",
        "agent_expected",
        "hr_tasks_have_materially_higher_stuck_rate",
        "ground_truth",
    ):
        assert token not in persisted.lower()
