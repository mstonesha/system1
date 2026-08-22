import inspect
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.clock import classify_day_part, working_days
from evaluation.generate import main
from evaluation.observe import terminal_task_records
from evaluation.planning import (
    completed_task_effort_records,
    effort_summary,
    unused_planned_breakdown,
    weekly_workload_rows,
)
from evaluation.scenarios import planning_workload as scenario
from evaluation.scenarios.planning_workload import (
    DEFAULT_SEED,
    HEAVY_WEEKS,
    START_DATE,
    WORKING_WEEKS,
    generate_planning_workload,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "planning_workload.yaml"
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_planning_workload,
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
def planning(eval_db):
    return unused_planned_breakdown(eval_db)


@pytest.fixture
def weeks(eval_db):
    return weekly_workload_rows(eval_db, START_DATE)


@pytest.fixture
def efforts(eval_db):
    return completed_task_effort_records(eval_db)


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
                "interrupted": work.interrupted,
            }
        )
    return rows


def _rate(rows, predicate, key="positive") -> float:
    matched = [row for row in rows if predicate(row)]
    assert matched
    return sum(row[key] for row in matched) / len(matched)


def _week_rate(rows: list[dict]) -> float:
    sessions = sum(row["session_count"] for row in rows)
    positive = sum(row["positive"] for row in rows)
    assert sessions
    return positive / sessions


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "planning_workload"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert set(payload["heavy_weeks"]) == set(HEAVY_WEEKS)
    assert "planned_daily_sessions_exceed_actual_sessions_overall" in (
        payload["expected_patterns"]
    )
    assert "heavy_planned_workload_weeks_have_lower_positive_outcome_rate" in (
        payload["expected_patterns"]
    )
    assert "no_long_term_improvement_or_decline" in (
        payload["expected_non_patterns"]
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "planning_workload.yaml" not in generate_source
    assert "planning_workload.yaml" not in scenario_source
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
    assert 1100 <= result.work_session_count <= 1500
    assert 180 <= result.task_count <= 280
    assert result.daily_task_count < result.work_session_count

    active_days = {
        row[0] for row in eval_db.query(DailyTask.date).distinct()
    }
    assert min(active_days) >= days[0]
    assert max(active_days) <= days[-1]
    assert len(active_days) >= 100


def test_daily_planned_sessions_stay_in_realistic_range(eval_db):
    values = [
        daily.planned_sessions
        for daily in eval_db.query(DailyTask)
        if daily.planned_sessions is not None
    ]
    assert values
    assert min(values) >= 1
    assert max(values) <= 5


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
        assert (
            daily.planned_sessions is None
            or 1 <= daily.planned_sessions <= 5
        )

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
    assert multi_session_days >= 20


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


def test_planned_sessions_exceed_actual_sessions(planning):
    gap = planning["planned_over_actual_gap"]
    assert 0.10 <= gap <= 0.32
    assert planning["planned_sessions"] > planning["actual_sessions"]
    assert 0.72 <= planning["execution_ratio"] <= 0.92


def test_unused_capacity_is_not_all_failure(planning):
    unused = planning["unused_planned_sessions"]
    active = planning["unused_while_task_remained_active"]
    benign = planning["unused_due_to_completion"]
    assert unused > 0
    assert benign >= 20
    assert active >= 20
    assert unused > active + 15
    assert benign / unused >= 0.15


def test_overcommit_early_completion_and_extra_work_all_occur(planning):
    assert planning["case_a_active_shortfall"] >= 20
    assert planning["case_b_early_completion"] >= 20
    assert planning["case_c_extra_work"] >= 15
    assert planning["case_d_executed_as_planned"] >= 15


def test_task_effort_is_mildly_underestimated(efforts):
    summary = effort_summary(efforts)
    assert summary["n"] >= 80
    assert summary["below"] >= 8
    assert summary["near"] >= 8
    assert summary["above"] >= 20
    assert 1.10 <= summary["mean_ratio"] <= 1.35
    assert summary["mean_actual"] > summary["mean_estimated"]


def test_estimation_error_is_not_just_failures(efforts):
    above = [row for row in efforts if row["band"] == "above"]
    assert above
    assert all(row["task"].status == "completed" for row in above)


def test_heavy_weeks_have_lower_positive_rate(weeks):
    heavy = [row for row in weeks if row["week"] in HEAVY_WEEKS]
    normal = [row for row in weeks if row["week"] not in HEAVY_WEEKS]
    assert len(weeks) == WORKING_WEEKS
    assert {row["week"] for row in weeks} == set(range(1, WORKING_WEEKS + 1))
    heavy_rate = _week_rate(heavy)
    normal_rate = _week_rate(normal)
    assert 0.46 <= heavy_rate <= 0.60
    assert 0.64 <= normal_rate <= 0.78
    assert normal_rate - heavy_rate >= 0.10
    for row in heavy:
        assert row["planned_sessions"] >= 70, row["week"]
    assert (
        sum(row["planned_sessions"] for row in heavy) / len(heavy)
        >= 78
    )
    for row in normal:
        assert row["planned_sessions"] <= 66


def test_heavy_periods_are_scattered(weeks):
    ordered = sorted(HEAVY_WEEKS)
    clusters = 1
    for previous, current in zip(ordered, ordered[1:]):
        if current > previous + 1:
            clusters += 1
    assert clusters >= 3
    assert min(ordered) <= 8
    assert max(ordered) >= 20
    first_half = [week for week in HEAVY_WEEKS if week <= 13]
    second_half = [week for week in HEAVY_WEEKS if week >= 14]
    assert first_half
    assert second_half


def test_planned_load_explains_outcomes_better_than_daily_task_count(
    weeks,
):
    high_planned = [
        row for row in weeks if row["planned_sessions"] >= 70
    ]
    low_planned = [
        row for row in weeks if row["planned_sessions"] <= 64
    ]
    planned_gap = _week_rate(low_planned) - _week_rate(high_planned)

    by_dailies = sorted(weeks, key=lambda row: row["daily_task_count"])
    high_dailies = by_dailies[-8:]
    low_dailies = by_dailies[:8]
    dailies_gap = _week_rate(low_dailies) - _week_rate(high_dailies)

    by_actual = sorted(weeks, key=lambda row: row["actual_sessions"])
    high_actual = by_actual[-8:]
    low_actual = by_actual[:8]
    actual_gap = _week_rate(low_actual) - _week_rate(high_actual)

    assert planned_gap >= 0.10
    assert planned_gap > dailies_gap + 0.03
    assert planned_gap >= actual_gap - 0.02

    many_small = [
        row for row in weeks if row["week"] in {5, 6, 18, 23}
    ]
    few_fat = [
        row for row in weeks if row["week"] in {12, 13, 19, 24}
    ]
    assert many_small and few_fat
    mean_many = sum(
        row["daily_task_count"] for row in many_small
    ) / len(many_small)
    mean_few = sum(row["daily_task_count"] for row in few_fat) / len(
        few_fat
    )
    assert mean_many > mean_few + 8
    assert abs(_week_rate(many_small) - _week_rate(few_fat)) <= 0.12


def test_no_meaningful_long_term_trend(weeks):
    early = [row for row in weeks if row["week"] <= 13]
    late = [row for row in weeks if row["week"] >= 14]
    assert abs(_week_rate(early) - _week_rate(late)) <= 0.08

    early_normal = [
        row for row in early if row["week"] not in HEAVY_WEEKS
    ]
    late_normal = [
        row for row in late if row["week"] not in HEAVY_WEEKS
    ]
    assert abs(_week_rate(early_normal) - _week_rate(late_normal)) <= 0.08


def test_no_strong_weekday_or_daypart_effect(eval_db):
    rows = _session_rows(eval_db)
    weekday_rates = [
        _rate(rows, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    part_rows = [
        row
        for row in rows
        if row["day_part"] in {
            "morning",
            "early_afternoon",
            "late_afternoon",
        }
    ]
    part_rates = [
        _rate(part_rows, lambda row, p=part: row["day_part"] == p)
        for part in ("morning", "early_afternoon", "late_afternoon")
    ]
    assert max(weekday_rates) - min(weekday_rates) <= 0.18
    assert max(part_rates) - min(part_rates) <= 0.18


def test_interruptions_do_not_explain_outcomes(eval_db):
    rows = _session_rows(eval_db)
    interrupted = _rate(rows, lambda row: row["interrupted"])
    uninterrupted = _rate(rows, lambda row: not row["interrupted"])
    assert abs(interrupted - uninterrupted) <= 0.12


def test_no_hr_or_category_stuck_pattern(eval_db):
    from evaluation.catalog import parse_task_category

    rows = []
    query = (
        eval_db.query(WorkSession, DailyTask, Task)
        .join(DailyTask, WorkSession.daily_task_id == DailyTask.id)
        .join(Task, DailyTask.task_id == Task.id)
    )
    for work, _daily, task in query:
        rows.append(
            {
                "stuck": work.outcome == "stuck",
                "category": parse_task_category(task.title),
            }
        )
    rates = []
    for category in {row["category"] for row in rows}:
        matched = [row for row in rows if row["category"] == category]
        if len(matched) < 40:
            continue
        rates.append(
            sum(row["stuck"] for row in matched) / len(matched)
        )
    assert rates
    assert max(rates) - min(rates) <= 0.16
    hr = [row for row in rows if row["category"] == "HR"]
    non_hr = [row for row in rows if row["category"] != "HR"]
    hr_stuck = sum(row["stuck"] for row in hr) / len(hr)
    other_stuck = sum(row["stuck"] for row in non_hr) / len(non_hr)
    assert abs(hr_stuck - other_stuck) <= 0.10


def test_no_strong_age_abandonment_gradient(eval_db):
    records = terminal_task_records(eval_db)
    young = [
        row for row in records if row["execution_age_days"] <= 7
    ]
    old = [
        row for row in records if row["execution_age_days"] >= 15
    ]
    if not young or not old:
        return
    young_rate = sum(row["abandoned"] for row in young) / len(young)
    old_rate = sum(row["abandoned"] for row in old) / len(old)
    assert abs(old_rate - young_rate) <= 0.22
    assert old_rate < 0.45
