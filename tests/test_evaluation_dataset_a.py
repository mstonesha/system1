import inspect
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)
from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import parse_task_category
from evaluation.clock import classify_day_part, working_days
from evaluation.export import export_evaluation_csv
from evaluation.generate import main
from evaluation.scenarios import temporal_patterns as scenario
from evaluation.scenarios.temporal_patterns import (
    DEFAULT_SEED,
    START_DATE,
    WORKING_WEEKS,
    generate_temporal_patterns,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "temporal_patterns.yaml"
)
IMPORTANT_DAY_PARTS = (
    "early_morning",
    "morning",
    "early_afternoon",
    "late_afternoon",
    "evening",
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_temporal_patterns,
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
                "category": parse_task_category(task.title),
                "task_age_days": (
                    daily.date
                    - task.created_at.astimezone(zone).date()
                ).days,
            }
        )
    return rows


def _rate(rows, predicate) -> float:
    matched = [row for row in rows if predicate(row)]
    assert matched
    return sum(row["positive"] for row in matched) / len(matched)


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "temporal_patterns"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert (
        "morning_hours_have_higher_positive_outcome_rate_than_afternoon_hours"
        in payload["expected_patterns"]
    )
    assert "monday_morning_is_a_strong_negative_exception" in (
        payload["expected_patterns"]
    )
    assert "do_not_infer_early_morning_is_best_from_its_small_sample_alone" in (
        payload["sample_size_cautions"]
    )
    assert "do_not_infer_evening_beats_morning_without_noting_its_small_sample" in (
        payload["sample_size_cautions"]
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "temporal_patterns.yaml" not in generate_source
    assert "interruptions_dependencies.yaml" not in generate_source
    assert "temporal_patterns.yaml" not in scenario_source
    assert "yaml.safe_load" not in scenario_source
    assert "yaml.safe_load" not in generate_source


def test_same_seed_is_reproducible(generated_eval):
    assert generated_eval["first_snap"] == generated_eval["second_snap"]
    assert (
        generated_eval["first_result"].task_count
        == generated_eval["result"].task_count
    )
    assert (
        generated_eval["first_result"].work_session_count
        == generated_eval["result"].work_session_count
    )


def test_alembic_schema_was_applied(eval_db):
    version = eval_db.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert version == "d4b8c1e0a2f5"


def test_volume_and_date_range_are_sensible(generated_eval, eval_db):
    result = generated_eval["result"]
    days = working_days(START_DATE, WORKING_WEEKS)

    assert result.start_date == days[0]
    assert result.end_date == days[-1]
    assert 500 <= result.work_session_count <= 700
    assert result.task_count < result.work_session_count
    assert result.daily_task_count < result.work_session_count
    assert result.daily_task_count >= 40

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

    parents = (
        eval_db.query(Task)
        .filter(Task.parent_task_id.is_not(None))
        .count()
    )
    assert parents >= 5


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


def test_positive_and_negative_outcomes_in_important_buckets(
    eval_db,
):
    rows = _session_rows(eval_db)
    buckets = {
        "monday_morning": lambda row: (
            row["weekday"] == 0 and row["day_part"] == "morning"
        ),
        "tue_fri_morning": lambda row: (
            row["weekday"] in {1, 2, 3, 4}
            and row["day_part"] == "morning"
        ),
        "late_afternoon": lambda row: (
            row["day_part"] == "late_afternoon"
        ),
    }
    for name in IMPORTANT_DAY_PARTS:
        buckets[name] = (
            lambda row, part=name: row["day_part"] == part
        )

    for name, predicate in buckets.items():
        matched = [row for row in rows if predicate(row)]
        outcomes = {row["work"].outcome for row in matched}
        assert matched, name
        assert outcomes & POSITIVE, name
        assert outcomes & NEGATIVE, name


def test_monday_morning_is_weaker_than_other_mornings(eval_db):
    rows = _session_rows(eval_db)
    monday_morning = _rate(
        rows,
        lambda row: row["weekday"] == 0
        and row["day_part"] == "morning",
    )
    tue_fri_morning = _rate(
        rows,
        lambda row: row["weekday"] in {1, 2, 3, 4}
        and row["day_part"] == "morning",
    )

    assert tue_fri_morning - monday_morning >= 0.18
    assert monday_morning < 0.55
    assert tue_fri_morning > 0.65


def test_tue_fri_morning_beats_late_afternoon(eval_db):
    rows = _session_rows(eval_db)
    tue_fri_morning = _rate(
        rows,
        lambda row: row["weekday"] in {1, 2, 3, 4}
        and row["day_part"] == "morning",
    )
    late_afternoon = _rate(
        rows,
        lambda row: row["day_part"] == "late_afternoon",
    )
    day_part_rates = {
        part: _rate(rows, lambda row, p=part: row["day_part"] == p)
        for part in IMPORTANT_DAY_PARTS
    }

    assert tue_fri_morning - late_afternoon >= 0.12
    morningish = _rate(
        rows,
        lambda row: row["day_part"] in {
            "early_morning",
            "morning",
        },
    )
    afternoonish = _rate(
        rows,
        lambda row: row["day_part"] in {
            "early_afternoon",
            "late_afternoon",
        },
    )
    assert morningish - afternoonish >= 0.08
    assert day_part_rates["morning"] > day_part_rates["late_afternoon"]
    assert (
        day_part_rates["early_morning"]
        > day_part_rates["late_afternoon"]
    )


def test_monday_is_not_uniformly_the_worst_day(eval_db):
    rows = _session_rows(eval_db)
    monday_afternoon = _rate(
        rows,
        lambda row: row["weekday"] == 0
        and row["day_part"] == "late_afternoon",
    )
    tue_fri_afternoon = _rate(
        rows,
        lambda row: row["weekday"] in {1, 2, 3, 4}
        and row["day_part"] == "late_afternoon",
    )
    monday_morning = _rate(
        rows,
        lambda row: row["weekday"] == 0
        and row["day_part"] == "morning",
    )

    assert abs(monday_afternoon - tue_fri_afternoon) < 0.20
    assert monday_afternoon > monday_morning


def test_interruptions_do_not_encode_the_temporal_pattern(eval_db):
    rows = _session_rows(eval_db)

    def interruption_rate(predicate) -> float:
        matched = [row for row in rows if predicate(row)]
        assert matched
        return sum(row["work"].interrupted for row in matched) / len(
            matched
        )

    by_part = {
        part: interruption_rate(
            lambda row, p=part: row["day_part"] == p
        )
        for part in IMPORTANT_DAY_PARTS
    }
    monday = interruption_rate(lambda row: row["weekday"] == 0)
    rest = interruption_rate(lambda row: row["weekday"] != 0)
    morning = interruption_rate(
        lambda row: row["day_part"] == "morning"
    )
    late = interruption_rate(
        lambda row: row["day_part"] == "late_afternoon"
    )

    assert max(by_part.values()) - min(by_part.values()) <= 0.12
    assert abs(monday - rest) <= 0.10
    assert abs(morning - late) <= 0.10


def test_category_does_not_encode_the_temporal_pattern(eval_db):
    rows = _session_rows(eval_db)
    assert all(row["category"] for row in rows)

    def distribution(predicate) -> Counter:
        matched = [row for row in rows if predicate(row)]
        counts = Counter(row["category"] for row in matched)
        total = sum(counts.values())
        return Counter(
            {key: value / total for key, value in counts.items()}
        )

    morning = distribution(lambda row: row["day_part"] == "morning")
    late = distribution(
        lambda row: row["day_part"] == "late_afternoon"
    )
    monday = distribution(lambda row: row["weekday"] == 0)
    rest = distribution(lambda row: row["weekday"] != 0)
    keys = set(morning) | set(late) | set(monday) | set(rest)
    morning_late_tvd = 0.5 * sum(
        abs(morning[key] - late[key]) for key in keys
    )
    monday_rest_tvd = 0.5 * sum(
        abs(monday[key] - rest[key]) for key in keys
    )

    assert morning_late_tvd <= 0.18
    assert monday_rest_tvd <= 0.18


def test_task_age_and_planned_sessions_are_neutral(eval_db):
    rows = _session_rows(eval_db)

    def mean_age(predicate) -> float:
        matched = [row["task_age_days"] for row in rows if predicate(row)]
        assert matched
        return sum(matched) / len(matched)

    def mean_planned(predicate) -> float:
        matched = [
            row["daily"].planned_sessions or 0
            for row in rows
            if predicate(row)
        ]
        assert matched
        return sum(matched) / len(matched)

    morning_age = mean_age(
        lambda row: row["day_part"] == "morning"
    )
    late_age = mean_age(
        lambda row: row["day_part"] == "late_afternoon"
    )
    monday_age = mean_age(lambda row: row["weekday"] == 0)
    rest_age = mean_age(lambda row: row["weekday"] != 0)
    morning_planned = mean_planned(
        lambda row: row["day_part"] == "morning"
    )
    late_planned = mean_planned(
        lambda row: row["day_part"] == "late_afternoon"
    )

    assert abs(morning_age - late_age) <= 4
    assert abs(monday_age - rest_age) <= 4
    assert abs(morning_planned - late_planned) <= 0.75


def test_csv_export_writes_expected_files(generated_eval, tmp_path):
    with generated_eval["SessionLocal"]() as db:
        output = export_evaluation_csv(db, tmp_path)

    assert (output / "tasks.csv").exists()
    assert (output / "daily_tasks.csv").exists()
    assert (output / "work_sessions.csv").exists()

    task_lines = (output / "tasks.csv").read_text().splitlines()
    daily_lines = (output / "daily_tasks.csv").read_text().splitlines()
    session_lines = (
        (output / "work_sessions.csv").read_text().splitlines()
    )

    assert task_lines[0].startswith("id,title,")
    assert daily_lines[0].startswith("id,task_id,date,")
    assert session_lines[0].startswith("id,daily_task_id,")
    assert len(task_lines) == generated_eval["result"].task_count + 1
    assert (
        len(daily_lines)
        == generated_eval["result"].daily_task_count + 1
    )
    assert (
        len(session_lines)
        == generated_eval["result"].work_session_count + 1
    )


def test_active_days_have_realistic_daily_task_counts(eval_db):
    counts = Counter(
        daily.date for daily in eval_db.query(DailyTask)
    )
    normal_days = [
        count for count in counts.values() if count >= 4
    ]
    assert normal_days
    assert max(counts.values()) <= 7
    assert min(counts.values()) >= 1


def _analytics_period(generated_eval, eval_db):
    result = generated_eval["result"]
    return (
        session_outcomes_by_weekday(
            eval_db,
            from_date=result.start_date,
            to_date=result.end_date,
        ),
        session_outcomes_by_daypart(
            eval_db,
            from_date=result.start_date,
            to_date=result.end_date,
        ),
        session_outcomes_by_weekday_daypart(
            eval_db,
            from_date=result.start_date,
            to_date=result.end_date,
        ),
    )


def test_analytics_exposes_dataset_a_weekday_pattern(
    generated_eval,
    eval_db,
):
    weekdays, _dayparts, _cells = _analytics_period(
        generated_eval,
        eval_db,
    )
    assert weekdays.total_sessions == (
        generated_eval["result"].work_session_count
    )
    assert [group.weekday for group in weekdays.groups] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    weakest = min(
        weekdays.groups,
        key=lambda group: group.positive_rate,
    )
    assert weakest.weekday == "Monday"
    for group in weekdays.groups:
        assert (
            group.positive_count + group.negative_count
            == group.session_count
        )
        assert abs(
            group.positive_rate + group.negative_rate - 1.0
        ) < 1e-12


def test_analytics_exposes_dataset_a_daypart_pattern(
    generated_eval,
    eval_db,
):
    _weekdays, dayparts, _cells = _analytics_period(
        generated_eval,
        eval_db,
    )
    by_part = {
        group.daypart: group for group in dayparts.groups
    }
    for name in IMPORTANT_DAY_PARTS:
        assert name in by_part
        assert by_part[name].session_count > 0

    morningish_n = (
        by_part["early_morning"].session_count
        + by_part["morning"].session_count
    )
    morningish_pos = (
        by_part["early_morning"].positive_count
        + by_part["morning"].positive_count
    )
    afternoonish_n = (
        by_part["early_afternoon"].session_count
        + by_part["late_afternoon"].session_count
    )
    afternoonish_pos = (
        by_part["early_afternoon"].positive_count
        + by_part["late_afternoon"].positive_count
    )
    morningish_rate = morningish_pos / morningish_n
    afternoonish_rate = afternoonish_pos / afternoonish_n

    assert morningish_rate - afternoonish_rate >= 0.08
    assert (
        by_part["morning"].positive_rate
        > by_part["late_afternoon"].positive_rate
    )
    assert (
        by_part["early_morning"].session_count
        < by_part["morning"].session_count
    )
    assert (
        by_part["evening"].session_count
        < by_part["morning"].session_count
    )


def test_analytics_reveals_monday_morning_exception(
    generated_eval,
    eval_db,
):
    _weekdays, dayparts, cells = _analytics_period(
        generated_eval,
        eval_db,
    )
    monday_morning = next(
        group
        for group in cells.groups
        if group.weekday == "Monday"
        and group.daypart == "morning"
    )
    tue_fri_morning = [
        group
        for group in cells.groups
        if group.weekday
        in {"Tuesday", "Wednesday", "Thursday", "Friday"}
        and group.daypart == "morning"
    ]
    tue_fri_n = sum(
        group.session_count for group in tue_fri_morning
    )
    tue_fri_pos = sum(
        group.positive_count for group in tue_fri_morning
    )
    tue_fri_rate = tue_fri_pos / tue_fri_n
    late_afternoon = next(
        group
        for group in dayparts.groups
        if group.daypart == "late_afternoon"
    )

    assert tue_fri_rate - monday_morning.positive_rate >= 0.18
    assert monday_morning.positive_rate < 0.55
    assert tue_fri_rate > 0.65
    assert tue_fri_rate - late_afternoon.positive_rate >= 0.12


def test_agent_temporal_evidence_hides_scenario_and_ground_truth(
    generated_eval,
    eval_db,
):
    from evaluation.agent.evidence import (
        build_temporal_evidence,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt

    result = generated_eval["result"]
    weekdays, dayparts, cells = _analytics_period(
        generated_eval,
        eval_db,
    )
    evidence = build_temporal_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_a",
    )
    blob = serialize_evidence(evidence)
    prompt = render_prompt(evidence).combined_text()
    monday_morning = next(
        row
        for row in evidence["weekday_daypart_outcomes"]
        if row["id"] == "weekday_daypart:monday:morning"
    )
    production_monday_morning = next(
        group
        for group in cells.groups
        if group.weekday == "Monday" and group.daypart == "morning"
    )
    assert evidence["case_id"] == "case_a"
    assert evidence["period"]["total_sessions"] == (
        weekdays.total_sessions
    )
    assert monday_morning["n"] == (
        production_monday_morning.session_count
    )
    assert monday_morning["positive_rate"] == (
        production_monday_morning.positive_rate
    )
    assert monday_morning["n"] > 0
    for token in (
        "temporal_patterns",
        "noise_control",
        "expected_patterns",
        "monday_morning_is_a_strong_negative_exception",
        "ground_truth",
    ):
        assert token not in blob
        assert token not in prompt
    assert "interruption" not in evidence
    assert "weekly_morning_afternoon" not in evidence
    assert len(evidence["weekday_outcomes"]) == len(weekdays.groups)
    assert len(evidence["daypart_outcomes"]) == len(dayparts.groups)


def test_agent_temporal_v2_weekly_evidence_matches_production(
    generated_eval,
    eval_db,
):
    from app.analytics.change import weekly_morning_afternoon_outcomes
    from evaluation.agent.evidence import (
        build_temporal_evidence,
        evidence_ids,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt

    result = generated_eval["result"]
    production = weekly_morning_afternoon_outcomes(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    evidence = build_temporal_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_a",
        version="temporal-v2",
    )
    weeks = evidence["weekly_morning_afternoon"]
    prompt = render_prompt(evidence, version="temporal-v2")
    blob = serialize_evidence(evidence)
    known = evidence_ids(evidence)
    assert weeks
    assert len(weeks) == len(production.groups)
    assert [row["week_start"] for row in weeks] == [
        group.week_start_date.isoformat()
        for group in production.groups
    ]
    for row, group in zip(weeks, production.groups):
        assert row["id"] == (
            f"week:{group.week_start_date.isoformat()}"
        )
        assert row["morning_n"] == group.morning_session_count
        assert row["morning_positive_rate"] == (
            group.morning_positive_rate
        )
        assert row["afternoon_n"] == group.afternoon_session_count
        assert row["afternoon_positive_rate"] == (
            group.afternoon_positive_rate
        )
        assert row["positive_rate_gap"] == group.positive_rate_gap
        assert row["id"] in known
        assert row["id"] in prompt.user
        assert "stable" not in row
        assert "confidence" not in row
        assert "significance" not in row
    assert prompt.version == "temporal-v2"
    assert "temporal-v2" in prompt.system
    assert "weekly_morning_afternoon" in blob
    for token in (
        "temporal_patterns",
        "noise_control",
        "expected_patterns",
        "monday_morning_is_a_strong_negative_exception",
        "ground_truth",
    ):
        assert token not in blob
        assert token not in prompt.combined_text()


def test_agent_temporal_v3_evidence_matches_v2(
    generated_eval,
    eval_db,
):
    from evaluation.agent.evidence import (
        build_temporal_evidence,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt

    result = generated_eval["result"]
    v2 = build_temporal_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_a",
        version="temporal-v2",
    )
    v3 = build_temporal_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_a",
        version="temporal-v3",
    )
    prompt = render_prompt(v3, version="temporal-v3")
    assert serialize_evidence(v2) == serialize_evidence(v3)
    assert "weekly_morning_afternoon" in v3
    assert prompt.version == "temporal-v3"
    assert serialize_evidence(v3) in prompt.user
    assert "temporal-v3" in prompt.system
    assert "converging" in prompt.system.lower()

