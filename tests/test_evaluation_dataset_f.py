import inspect
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.age import AGE_BUCKETS
from evaluation.catalog import parse_task_category
from evaluation.clock import working_days
from evaluation.generate import main
from evaluation.observe import session_observation_rows, terminal_task_records
from evaluation.planning import weekly_workload_rows
from evaluation.scenarios import noise_control as scenario
from evaluation.scenarios.noise_control import (
    BASELINE_POSITIVE_RATE,
    DEFAULT_SEED,
    START_DATE,
    WORKING_WEEKS,
    generate_noise_control,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
HIGH_VOLUME_DAY_PARTS = (
    "morning",
    "early_afternoon",
    "late_afternoon",
)
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "noise_control.yaml"
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_noise_control,
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
def rows(eval_db):
    matched = session_observation_rows(eval_db)
    assert matched
    return matched


def _rate(items, predicate, key="positive") -> float:
    matched = [row for row in items if predicate(row)]
    assert matched
    return sum(row[key] for row in matched) / len(matched)


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "noise_control"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert payload["baseline"]["positive_rate"] == BASELINE_POSITIVE_RATE
    assert "no_robust_behavioural_pattern" in payload["expected_patterns"]
    assert "no_hr_stuck_cluster" in payload["expected_non_patterns"]
    assert "a best weekday based solely on ranking" in (
        payload["agent_should_not_claim"]
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "noise_control.yaml" not in generate_source
    assert "noise_control.yaml" not in scenario_source
    assert "yaml.safe_load" not in scenario_source
    assert "yaml.safe_load" not in generate_source


def test_same_seed_is_reproducible(generated_eval):
    assert generated_eval["first_snap"] == generated_eval["second_snap"]
    assert (
        generated_eval["first_result"].work_session_count
        == generated_eval["result"].work_session_count
    )


def test_volume_and_invariants(generated_eval, eval_db):
    from zoneinfo import ZoneInfo

    result = generated_eval["result"]
    days = working_days(START_DATE, WORKING_WEEKS)
    zone = ZoneInfo(get_settings().timezone)

    assert result.start_date == days[0]
    assert result.end_date == days[-1]
    assert 750 <= result.work_session_count <= 1050
    assert 130 <= result.task_count <= 200

    version = eval_db.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert version == "f1a9b3c4d5e6"

    task_ids = {task.id for task in eval_db.query(Task)}
    seen_pairs: set[tuple[int, date]] = set()
    planned = []
    for daily in eval_db.query(DailyTask):
        assert daily.date.weekday() < 5
        assert daily.task_id in task_ids
        pair = (daily.task_id, daily.date)
        assert pair not in seen_pairs
        seen_pairs.add(pair)
        if daily.planned_sessions is not None:
            planned.append(daily.planned_sessions)
            assert 1 <= daily.planned_sessions <= 5
    assert planned
    assert min(planned) >= 1
    assert max(planned) <= 5

    for work in eval_db.query(WorkSession):
        local = work.started_at.astimezone(zone)
        assert local.weekday() < 5
        assert work.ended_at is not None
        assert work.ended_at >= work.started_at
        assert work.session_state == "completed"
        assert work.outcome in POSITIVE | NEGATIVE

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
    assert multi_session_days >= 10
    assert result.daily_task_count <= result.work_session_count

    daily = eval_db.query(DailyTask).first()
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


def test_large_buckets_have_enough_sessions(rows):
    overall = sum(row["positive"] for row in rows) / len(rows)
    assert 0.58 <= overall <= 0.72
    for weekday in range(5):
        matched = [row for row in rows if row["weekday"] == weekday]
        assert len(matched) >= 80
    for part in HIGH_VOLUME_DAY_PARTS:
        matched = [row for row in rows if row["day_part"] == part]
        assert len(matched) >= 150


def test_weekday_and_high_volume_dayparts_stay_neutral(rows):
    weekday_rates = [
        _rate(rows, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    part_rates = [
        _rate(rows, lambda row, p=part: row["day_part"] == p)
        for part in HIGH_VOLUME_DAY_PARTS
    ]
    assert max(weekday_rates) - min(weekday_rates) <= 0.16
    assert max(part_rates) - min(part_rates) <= 0.14
    assert max(weekday_rates) != min(weekday_rates)
    assert max(part_rates) != min(part_rates)


def test_interruptions_and_hr_remain_neutral(rows):
    interrupted = _rate(rows, lambda row: row["work"].interrupted)
    uninterrupted = _rate(rows, lambda row: not row["work"].interrupted)
    assert abs(interrupted - uninterrupted) <= 0.10

    hr = [row for row in rows if row["category"] == "HR"]
    non_hr = [row for row in rows if row["category"] != "HR"]
    assert hr and non_hr
    hr_stuck = sum(row["stuck"] for row in hr) / len(hr)
    other_stuck = sum(row["stuck"] for row in non_hr) / len(non_hr)
    assert abs(hr_stuck - other_stuck) <= 0.10
    assert any("await" in row["title"].lower() for row in hr)
    assert any(row["stuck"] for row in hr)


def test_no_strong_age_abandonment_gradient(eval_db):
    records = terminal_task_records(eval_db)
    counts = {
        name: len(
            [row for row in records if row["execution_bucket"] == name]
        )
        for name, _low, _high in AGE_BUCKETS
    }
    populated = [name for name, count in counts.items() if count >= 6]
    assert len(populated) >= 4
    assert counts["31+"] >= 4
    assert counts["0-2"] >= 4 or counts["3-7"] >= 8

    young = [row for row in records if row["execution_age_days"] <= 7]
    old = [row for row in records if row["execution_age_days"] >= 15]
    assert young and old
    young_rate = sum(row["abandoned"] for row in young) / len(young)
    old_rate = sum(row["abandoned"] for row in old) / len(old)
    assert old_rate < young_rate + 0.18
    assert abs(old_rate - young_rate) <= 0.30
    rates = []
    for name, _low, _high in AGE_BUCKETS:
        matched = [
            row for row in records if row["execution_bucket"] == name
        ]
        if len(matched) < 6:
            continue
        rates.append(sum(row["abandoned"] for row in matched) / len(matched))
    if len(rates) >= 3:
        assert max(rates) - min(rates) <= 0.35


def test_workload_and_time_do_not_show_planted_effects(eval_db, rows):
    weeks = weekly_workload_rows(eval_db, START_DATE)
    assert len(weeks) >= 16
    ordered = sorted(weeks, key=lambda row: row["planned_sessions"])
    low = ordered[:4]
    high = ordered[-4:]

    def _week_rate(chunk):
        sessions = sum(row["session_count"] for row in chunk)
        positive = sum(row["positive"] for row in chunk)
        assert sessions
        return positive / sessions

    assert abs(_week_rate(high) - _week_rate(low)) <= 0.14

    early = [row for row in rows if row["local"].date() < date(2027, 7, 26)]
    late = [row for row in rows if row["local"].date() >= date(2027, 7, 26)]
    assert early and late
    early_rate = sum(row["positive"] for row in early) / len(early)
    late_rate = sum(row["positive"] for row in late) / len(late)
    assert abs(early_rate - late_rate) <= 0.10


def test_realised_noise_includes_useful_false_leads(eval_db, rows):
    overall = sum(row["positive"] for row in rows) / len(rows)
    weekday_rates = [
        _rate(rows, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    leads = []
    if max(weekday_rates) - min(weekday_rates) >= 0.05:
        leads.append("weekday_ranking")

    for part in ("early_morning", "evening"):
        matched = [row for row in rows if row["day_part"] == part]
        if len(matched) < 20:
            continue
        rate = sum(row["positive"] for row in matched) / len(matched)
        central = [
            row for row in rows if row["day_part"] in HIGH_VOLUME_DAY_PARTS
        ]
        if abs(rate - overall) >= 0.07 and len(matched) < 0.5 * len(central):
            leads.append("small_daypart")
            break

    interrupted = _rate(rows, lambda row: row["work"].interrupted)
    uninterrupted = _rate(rows, lambda row: not row["work"].interrupted)
    if 0.015 <= abs(interrupted - uninterrupted) <= 0.10:
        leads.append("interruption_offset")

    stuck_rates = []
    for category in {row["category"] for row in rows}:
        matched = [row for row in rows if row["category"] == category]
        if len(matched) < 40:
            continue
        stuck_rates.append(
            sum(row["stuck"] for row in matched) / len(matched)
        )
    if stuck_rates and max(stuck_rates) - min(stuck_rates) >= 0.05:
        leads.append("category_stuck_spread")

    records = terminal_task_records(eval_db)
    bucket_rates = []
    for name, _low, _high in AGE_BUCKETS:
        matched = [
            row for row in records if row["execution_bucket"] == name
        ]
        if len(matched) < 6:
            continue
        bucket_rates.append(
            sum(row["abandoned"] for row in matched) / len(matched)
        )
    if len(bucket_rates) >= 2:
        neighbour_gaps = [
            abs(bucket_rates[index + 1] - bucket_rates[index])
            for index in range(len(bucket_rates) - 1)
        ]
        if neighbour_gaps and max(neighbour_gaps) >= 0.08:
            leads.append("age_bucket_noise")

    weeks = weekly_workload_rows(eval_db, START_DATE)
    if len(weeks) >= 6:
        rates = [row["positive_rate"] for row in weeks]
        improved = any(
            rates[index + 2] - rates[index] >= 0.12
            for index in range(len(rates) - 2)
        )
        declined = any(
            rates[index] - rates[index + 2] >= 0.12
            for index in range(len(rates) - 2)
        )
        if improved or declined:
            leads.append("short_run")

        by_planned = sorted(weeks, key=lambda row: row["planned_sessions"])
        if by_planned[-1]["positive_rate"] >= overall + 0.05:
            leads.append("busy_good_week")
        if by_planned[0]["positive_rate"] <= overall - 0.05:
            leads.append("quiet_poor_week")

    assert len(leads) >= 3, leads
