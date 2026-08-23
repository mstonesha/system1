import inspect
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.task_age import (
    classify_execution_age_days,
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.age import AGE_BUCKETS
from evaluation.clock import working_days
from evaluation.generate import main
from evaluation.observe import terminal_task_records
from evaluation.scenarios import task_age_abandonment as scenario
from evaluation.scenarios.task_age_abandonment import (
    DEFAULT_SEED,
    START_DATE,
    WORKING_WEEKS,
    generate_task_age_abandonment,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "task_age_abandonment.yaml"
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_task_age_abandonment,
        DEFAULT_SEED,
    )


@pytest.fixture
def eval_db(generated_eval):
    session = generated_eval["SessionLocal"]()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def terminals(eval_db):
    records = terminal_task_records(eval_db)
    assert records
    return records


def _local_zone():
    from zoneinfo import ZoneInfo

    return ZoneInfo(get_settings().timezone)


def _rate(rows, predicate) -> float:
    matched = [row for row in rows if predicate(row)]
    assert matched
    return sum(row["abandoned"] for row in matched) / len(matched)


def _bucket(records, name: str) -> list[dict]:
    return [row for row in records if row["execution_bucket"] == name]


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "task_age_abandonment"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert payload["age_definition"]["start"] == (
        "first_non_removed_daily_task_date"
    )
    assert payload["agent_evidence_contract"] == "task-age-v1"
    generator_truth = payload["generator_truth"]
    assert "abandonment_probability_rises_with_execution_age" in (
        generator_truth
    )
    assert "first_today_commitment_is_more_informative_than_task_created_at" in (
        generator_truth
    )
    assert "expected_patterns" not in payload


def test_agent_evaluable_ground_truth_matches_evidence_contract():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())
    agent_patterns = payload["agent_expected_patterns"]
    agent_hypotheses = payload["agent_expected_hypotheses"]
    agent_insufficient = payload["agent_expected_insufficient_evidence"]
    able_blob = " ".join(payload["agent_should_be_able_to_say"]).lower()
    must_blob = " ".join(payload["agent_should_not_claim"]).lower()
    hidden = set(payload["not_agent_evaluable"])

    assert "abandonment_probability_rises_with_execution_age" in (
        agent_patterns
    )
    assert "older_execution_age_buckets_show_materially_higher_abandonment" in (
        agent_patterns
    )
    assert "relationship_is_a_population_association_not_a_deterministic_rule" in (
        agent_patterns
    )
    assert hidden.isdisjoint(agent_patterns)
    assert hidden.isdisjoint(agent_hypotheses)
    assert "first_today_commitment_is_more_informative_than_task_created_at" in (
        hidden
    )
    assert "created_at_age_is_a_weaker_abandonment_signal" in hidden
    assert "abandonment rates generally increase" in able_blob
    assert "especially substantial in the older buckets" in able_blob
    assert "not a deterministic rule" in able_blob
    assert "older tasks can still complete" in able_blob
    assert "younger tasks can still be abandoned" in able_blob
    assert "does not establish that execution age causes" in able_blob
    assert "not task creation age" in able_blob
    assert "reopened or undatable" in able_blob
    assert "ageing causes abandonment" in must_blob
    assert "tasks older than 31 days will be abandoned" in must_blob
    assert "age since task record creation" in must_blob
    assert "execution_age_does_not_have_a_demonstrated_causal_effect" in (
        agent_insufficient
    )
    assert "supplied_metric_is_not_age_since_task_record_creation" in (
        agent_insufficient
    )
    assert "longer_execution_age_may_reflect_unresolved_obstacles_deprioritisation_or_difficulty" in (
        agent_hypotheses
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "task_age_abandonment.yaml" not in generate_source
    assert "task_age_abandonment.yaml" not in scenario_source
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
    assert version == "f1a9b3c4d5e6"


def test_volume_and_date_range_are_sensible(generated_eval, eval_db):
    result = generated_eval["result"]
    days = working_days(START_DATE, WORKING_WEEKS)

    assert result.start_date == days[0]
    assert result.end_date == days[-1]
    assert 900 <= result.work_session_count <= 1200
    assert 150 <= result.task_count <= 220
    assert result.daily_task_count < result.work_session_count

    active_days = {
        row[0] for row in eval_db.query(DailyTask.date).distinct()
    }
    assert min(active_days) >= days[0]
    assert max(active_days) <= days[-1]


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


def test_core_relational_constraints(eval_db):
    task_ids = {task.id for task in eval_db.query(Task)}
    seen_pairs: set[tuple[int, date]] = set()

    for daily in eval_db.query(DailyTask):
        assert daily.task_id in task_ids
        pair = (daily.task_id, daily.date)
        assert pair not in seen_pairs
        seen_pairs.add(pair)

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
        assert task.completed_at is not None

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
        assert task.completed_at is None

    spanning = len(
        eval_db.query(DailyTask.task_id)
        .group_by(DailyTask.task_id)
        .having(func.count(DailyTask.id) > 1)
        .all()
    )
    assert spanning >= 20

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
        WorkSession(daily_task_id=daily.id, session_state="running")
    )
    eval_db.flush()
    eval_db.add(
        WorkSession(daily_task_id=daily.id, session_state="running")
    )
    with pytest.raises(IntegrityError):
        eval_db.flush()
    eval_db.rollback()


def test_each_age_bucket_has_enough_terminal_tasks(terminals):
    for name, _low, _high in AGE_BUCKETS:
        matched = _bucket(terminals, name)
        assert len(matched) >= 20, name


def test_abandonment_rises_with_execution_age(terminals):
    """Generator validation: planted execution-age abandonment gradient."""
    rates = [
        _rate(terminals, lambda row, n=name: row["execution_bucket"] == n)
        for name, _low, _high in AGE_BUCKETS
    ]
    young = _rate(
        terminals,
        lambda row: row["execution_age_days"] <= 7,
    )
    over_14 = _rate(
        terminals,
        lambda row: row["execution_age_days"] > 14,
    )
    old = _rate(
        terminals,
        lambda row: row["execution_age_days"] >= 31,
    )
    mid = _rate(
        terminals,
        lambda row: 15 <= row["execution_age_days"] <= 30,
    )

    inversions = sum(
        1
        for index in range(len(rates) - 1)
        if rates[index + 1] + 0.04 < rates[index]
    )
    assert inversions <= 1
    assert rates[-1] == max(rates) or rates[-1] >= max(rates) - 0.05
    assert old - young >= 0.28
    assert over_14 - young >= 0.12
    assert mid > young
    assert old > 0.40
    assert young < 0.18


def test_old_tasks_still_complete_and_young_tasks_are_abandoned(
    terminals,
):
    old_complete = [
        row
        for row in terminals
        if row["execution_age_days"] >= 31 and not row["abandoned"]
    ]
    young_abandoned = [
        row
        for row in terminals
        if row["execution_age_days"] <= 7 and row["abandoned"]
    ]
    first_days_abandoned = [
        row
        for row in terminals
        if row["execution_age_days"] <= 2 and row["abandoned"]
    ]
    assert len(old_complete) >= 8
    assert len(young_abandoned) >= 2
    assert first_days_abandoned


def test_execution_age_is_stronger_than_created_at_age(terminals):
    """Generator truth: created-at age is a weaker/misleading alternative.

    This comparison is not agent-evaluable under task-age-v1.
    """
    exec_gap = _rate(
        terminals,
        lambda row: row["execution_age_days"] >= 31,
    ) - _rate(
        terminals,
        lambda row: row["execution_age_days"] <= 7,
    )
    created_gap = _rate(
        terminals,
        lambda row: row["created_age_days"] >= 31,
    ) - _rate(
        terminals,
        lambda row: row["created_age_days"] <= 7,
    )
    mixed = [
        row
        for row in terminals
        if row["execution_age_days"] <= 7
        and row["backlog_days"] >= 14
    ]

    assert exec_gap >= 0.28
    assert exec_gap > created_gap + 0.06
    assert mixed
    mixed_rate = sum(row["abandoned"] for row in mixed) / len(mixed)
    young_exec = _rate(
        terminals,
        lambda row: row["execution_age_days"] <= 7,
    )
    assert abs(mixed_rate - young_exec) <= 0.15
    assert mixed_rate < 0.25


def test_some_tasks_have_a_backlog_before_today(terminals):
    delayed = [
        row for row in terminals if row["backlog_days"] >= 8
    ]
    same_day = [
        row for row in terminals if row["backlog_days"] == 0
    ]
    long_delay = [
        row for row in terminals if row["backlog_days"] >= 30
    ]
    assert delayed
    assert same_day
    assert long_delay
    assert any(
        row["execution_age_days"] <= 7 and row["backlog_days"] >= 14
        for row in terminals
    )


def test_category_does_not_explain_abandonment(terminals):
    rates = []
    for category in {row["category"] for row in terminals}:
        matched = [
            row for row in terminals if row["category"] == category
        ]
        if len(matched) < 12:
            continue
        rates.append(
            sum(row["abandoned"] for row in matched) / len(matched)
        )
    assert rates
    assert max(rates) - min(rates) <= 0.22


def test_interruptions_do_not_explain_abandonment(terminals):
    interrupted = _rate(
        terminals,
        lambda row: row["last_interrupted"],
    )
    uninterrupted = _rate(
        terminals,
        lambda row: not row["last_interrupted"],
    )
    abandoned = [row for row in terminals if row["abandoned"]]
    completed = [row for row in terminals if not row["abandoned"]]
    abandoned_share = sum(
        row["interrupted_share"] for row in abandoned
    ) / len(abandoned)
    completed_share = sum(
        row["interrupted_share"] for row in completed
    ) / len(completed)

    assert abs(interrupted - uninterrupted) <= 0.15
    assert abs(abandoned_share - completed_share) <= 0.08


def test_weekday_and_daypart_do_not_explain_abandonment(terminals):
    weekday_rates = [
        _rate(terminals, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    part_rows = [
        row for row in terminals if row["day_part"] in {
            "morning",
            "early_afternoon",
            "late_afternoon",
        }
    ]
    part_rates = [
        _rate(part_rows, lambda row, p=part: row["day_part"] == p)
        for part in ("morning", "early_afternoon", "late_afternoon")
    ]
    assert max(weekday_rates) - min(weekday_rates) <= 0.22
    assert max(part_rates) - min(part_rates) <= 0.22


def test_planned_sessions_do_not_explain_abandonment(terminals):
    low = _rate(
        terminals,
        lambda row: row["planned_sessions"] <= 2,
    )
    high = _rate(
        terminals,
        lambda row: row["planned_sessions"] >= 3,
    )
    assert abs(high - low) <= 0.15


def test_some_tasks_skip_days_on_today(eval_db, terminals):
    gapped = 0
    for row in terminals:
        dailies = sorted(
            eval_db.query(DailyTask)
            .filter(DailyTask.task_id == row["task"].id)
            .all(),
            key=lambda item: item.date,
        )
        if len(dailies) < 2:
            continue
        span_days = (dailies[-1].date - dailies[0].date).days
        if span_days + 1 > len(dailies) + 2:
            gapped += 1
    assert gapped >= 10


def test_analytics_recovers_dataset_c_age_gradient(
    generated_eval,
    eval_db,
):
    result = generated_eval["result"]
    analysis = task_abandonment_by_execution_age(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    by_bucket = {
        group.age_bucket: group for group in analysis.groups
    }
    assert [group.age_bucket for group in analysis.groups] == [
        "0-2",
        "3-7",
        "8-14",
        "15-30",
        "31+",
    ]
    young_n = (
        by_bucket["0-2"].terminal_task_count
        + by_bucket["3-7"].terminal_task_count
    )
    young_abandoned = (
        by_bucket["0-2"].abandoned_count
        + by_bucket["3-7"].abandoned_count
    )
    young_rate = young_abandoned / young_n
    old = by_bucket["31+"]
    assert old.abandonment_rate - young_rate >= 0.28
    assert old.abandonment_rate > 0.40
    assert young_rate < 0.18
    assert old.completed_count >= 8
    assert (
        by_bucket["0-2"].abandoned_count
        + by_bucket["3-7"].abandoned_count
        >= 2
    )
    forbidden = {
        "risk",
        "threshold",
        "warning",
        "significance",
        "confidence",
        "recommendation",
        "likely_to_fail",
    }
    assert forbidden.isdisjoint(
        analysis.__dataclass_fields__
    )


def test_analytics_execution_age_beats_created_at_on_dataset_c(
    generated_eval,
    eval_db,
):
    """Generator validation: execution age beats created-at age.

    Not an agent-evaluable conclusion under task-age-v1.
    """
    from zoneinfo import ZoneInfo

    result = generated_eval["result"]
    details = terminal_tasks_with_execution_age(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    zone = ZoneInfo(get_settings().timezone)

    def _rate_for(age_days, predicate):
        matched = [
            row
            for row in details
            if predicate(age_days(row))
        ]
        assert matched
        abandoned = sum(
            1
            for row in matched
            if row.terminal_outcome == "abandoned"
        )
        return abandoned / len(matched)

    def execution_age(row):
        return row.execution_age_days

    def created_age(row):
        task = eval_db.get(Task, row.task_id)
        created = task.created_at.astimezone(zone).date()
        return max(0, (row.terminal_date - created).days)

    exec_gap = _rate_for(
        execution_age,
        lambda days: days >= 31,
    ) - _rate_for(
        execution_age,
        lambda days: days <= 7,
    )
    created_gap = _rate_for(
        created_age,
        lambda days: days >= 31,
    ) - _rate_for(
        created_age,
        lambda days: days <= 7,
    )
    assert exec_gap >= 0.28
    assert exec_gap > created_gap + 0.06
    assert classify_execution_age_days(7) == "3-7"


def test_agent_task_age_evidence_hides_scenario_and_created_at(
    generated_eval,
    eval_db,
):
    import json

    from app.analytics.task_age import EXECUTION_AGE_BUCKETS
    from evaluation.agent.evidence import (
        build_task_age_evidence,
        evidence_ids,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt
    from evaluation.agent.runner import run_case

    result = generated_eval["result"]
    production = task_abandonment_by_execution_age(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    details = terminal_tasks_with_execution_age(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    evidence = build_task_age_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_c",
    )
    assert evidence["case_id"] == "case_c"
    assert evidence["period"]["total_terminal_tasks"] == (
        production.total_terminal_tasks
    )
    assert [
        row["bucket"] for row in evidence["execution_age_abandonment"]
    ] == [spec.name for spec in EXECUTION_AGE_BUCKETS]
    for row, group in zip(
        evidence["execution_age_abandonment"],
        production.groups,
    ):
        assert row["n"] == group.terminal_task_count
        assert (
            row["completed_count"] + row["abandoned_count"]
            == row["n"]
        )
        assert row["abandonment_rate"] == group.abandonment_rate
    drilldown = evidence["terminal_task_drilldown"]
    assert drilldown["returned_task_count"] == len(details)
    assert drilldown["returned_task_count"] == (
        production.total_terminal_tasks
    )
    assert "limit" not in drilldown
    assert all("title" not in row for row in drilldown["tasks"])
    blob = serialize_evidence(evidence)
    prompt = render_prompt(
        evidence,
        version="task-age-v1",
    ).combined_text()
    for token in (
        "task_age_abandonment",
        "expected_patterns",
        "generator_truth",
        "created_at",
        "first_today_commitment_is_more_informative_than_task_created_at",
        "ground_truth",
        "dataset c",
    ):
        assert token not in blob.lower()
        assert token not in prompt.lower()
    known = evidence_ids(evidence)
    assert "execution_age:31+" in known
    assert drilldown["tasks"][0]["id"] in known
    record = run_case(
        eval_db,
        case_id="case_c",
        from_date=result.start_date,
        to_date=result.end_date,
        dry_run=True,
        model=None,
        prompt_version="task-age-v1",
    )
    assert record["case_id"] == "case_c"
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    assert "created_at" not in persisted.lower()
    assert "task_age_abandonment" not in persisted.lower()
