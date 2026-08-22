import inspect
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import parse_task_category
from evaluation.change import (
    focus_session_rows,
    last_n_weeks,
    morning_afternoon_summary,
    preceding_n_weeks,
    rolling_gap,
    rows_for_weeks,
    weekly_focus_rows,
)
from evaluation.clock import working_days
from evaluation.generate import main
from evaluation.observe import terminal_task_records
from evaluation.planning import weekly_workload_rows
from evaluation.scenarios import behaviour_change as scenario
from evaluation.scenarios.behaviour_change import (
    DEFAULT_SEED,
    DECOY_WEEK,
    HISTORICAL_WEEKS,
    RECENT_WEEKS,
    START_DATE,
    TRANSITION_WEEKS,
    WORKING_WEEKS,
    generate_behaviour_change,
)
from tests.evaluation_helpers import prepared_eval


POSITIVE = frozenset({"progress", "complete"})
NEGATIVE = frozenset({"stuck", "paused", "abandoned"})
GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "ground_truth"
    / "behaviour_change.yaml"
)


@pytest.fixture(scope="module")
def generated_eval():
    yield from prepared_eval(
        generate_behaviour_change,
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
    matched = focus_session_rows(eval_db)
    assert matched
    return matched


@pytest.fixture
def weekly(rows):
    return weekly_focus_rows(rows, START_DATE)


def _rate(items, predicate, key="positive") -> float:
    matched = [row for row in items if predicate(row)]
    assert matched
    return sum(row[key] for row in matched) / len(matched)


def test_ground_truth_matches_generator_constants():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())

    assert payload["scenario"] == "behaviour_change"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["date_range"]["start"] == START_DATE.isoformat()
    expected_end = working_days(START_DATE, WORKING_WEEKS)[-1]
    assert payload["date_range"]["end"] == expected_end.isoformat()
    assert payload["date_range"]["working_weeks"] == WORKING_WEEKS
    assert payload["phases"]["historical"] == list(HISTORICAL_WEEKS)
    assert payload["phases"]["transition"] == list(TRANSITION_WEEKS)
    assert payload["phases"]["recent"] == list(RECENT_WEEKS)
    assert payload["decoy_week"] == DECOY_WEEK
    assert "historical_morning_advantage_is_strong" in (
        payload["expected_patterns"]
    )
    assert "lifetime_average_overstates_current_morning_advantage" in (
        payload["expected_patterns"]
    )
    assert "no_meaningful_weekday_effect" in (
        payload["expected_non_patterns"]
    )
    assert "no_workload_regime_explaining_the_change" in (
        payload["expected_non_patterns"]
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "behaviour_change.yaml" not in generate_source
    assert "behaviour_change.yaml" not in scenario_source
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
    assert 1400 <= result.work_session_count <= 1900
    assert 250 <= result.task_count <= 350
    assert result.daily_task_count < result.work_session_count

    active_days = {
        row[0] for row in eval_db.query(DailyTask.date).distinct()
    }
    assert min(active_days) >= days[0]
    assert max(active_days) <= days[-1]
    assert len(active_days) >= 160


def test_no_weekend_daily_tasks_or_sessions(eval_db):
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(get_settings().timezone)

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
    assert spanning >= 40

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


def test_some_tasks_span_the_transition(eval_db):
    spanning = 0
    for task_id, in eval_db.query(DailyTask.task_id).distinct():
        dates = [
            row[0]
            for row in eval_db.query(DailyTask.date)
            .filter(DailyTask.task_id == task_id)
            .all()
        ]
        weeks = {
            (day - START_DATE).days // 7 + 1 for day in dates
        }
        if any(week <= 22 for week in weeks) and any(
            week >= 23 for week in weeks
        ):
            spanning += 1
    assert spanning >= 8


def test_historical_and_recent_windows_have_enough_sessions(rows):
    historical = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 1, 22)
    )
    recent = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 29, 36)
    )
    last_eight = morning_afternoon_summary(
        last_n_weeks(rows, START_DATE, 8)
    )

    assert historical["morning_n"] >= 300
    assert historical["afternoon_n"] >= 300
    assert recent["morning_n"] >= 100
    assert recent["afternoon_n"] >= 100
    assert last_eight["morning_n"] >= 100
    assert last_eight["afternoon_n"] >= 100

    other = [row for row in rows if row["period"] == "other"]
    focus = [row for row in rows if row["period"] != "other"]
    assert len(other) < 0.18 * len(rows)
    assert len(focus) > len(other) * 4


def test_historical_morning_advantage_is_strong(rows):
    summary = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 1, 22)
    )
    assert 0.70 <= summary["morning_rate"] <= 0.84
    assert 0.40 <= summary["afternoon_rate"] <= 0.56
    assert summary["gap"] >= 0.18


def test_lifetime_still_favours_mornings(rows):
    summary = morning_afternoon_summary(rows)
    assert summary["morning_rate"] > summary["afternoon_rate"]
    assert summary["gap"] >= 0.08
    assert 0.64 <= summary["morning_rate"] <= 0.78
    assert 0.46 <= summary["afternoon_rate"] <= 0.62


def test_recent_morning_advantage_is_absent_or_small(rows):
    last_six = morning_afternoon_summary(
        last_n_weeks(rows, START_DATE, 6)
    )
    last_eight = morning_afternoon_summary(
        last_n_weeks(rows, START_DATE, 8)
    )
    recent = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 29, 36)
    )
    assert abs(last_six["gap"]) <= 0.12
    assert last_six["gap"] <= 0.06
    assert last_eight["gap"] <= 0.10
    assert abs(last_eight["gap"]) <= 0.14
    assert recent["gap"] <= 0.10
    assert abs(recent["gap"]) <= 0.14


def test_window_choice_shows_a_weakening_progression(rows):
    lifetime = morning_afternoon_summary(rows)
    last_sixteen = morning_afternoon_summary(
        last_n_weeks(rows, START_DATE, 16)
    )
    last_eight = morning_afternoon_summary(
        last_n_weeks(rows, START_DATE, 8)
    )
    preceding = morning_afternoon_summary(
        preceding_n_weeks(
            rows,
            START_DATE,
            recent_weeks=8,
            preceding_weeks=16,
        )
    )
    historical = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 1, 22)
    )
    transition = morning_afternoon_summary(
        rows_for_weeks(rows, START_DATE, 23, 28)
    )

    assert lifetime["gap"] > last_sixteen["gap"] + 0.02
    assert last_sixteen["gap"] > last_eight["gap"] - 0.02
    assert preceding["gap"] >= 0.08
    assert transition["gap"] < historical["gap"] - 0.04
    assert transition["gap"] < lifetime["gap"] + 0.02


def test_weekly_series_shows_a_broader_change_not_one_outlier(weekly):
    historical = [row for row in weekly if row["week"] <= 22]
    recent = [
        row
        for row in weekly
        if 29 <= row["week"] <= 36 and row["week"] != DECOY_WEEK
    ]
    historical_strong = [
        row for row in historical if row["gap"] >= 0.10
    ]
    recent_small = [row for row in recent if row["gap"] < 0.18]
    mean_historical = sum(row["gap"] for row in historical) / len(
        historical
    )
    mean_recent = sum(row["gap"] for row in recent) / len(recent)

    assert len(historical_strong) >= 12
    assert len(recent_small) >= 4
    assert mean_historical - mean_recent >= 0.12
    rolled = rolling_gap(weekly, 6)
    early_roll = [row for row in rolled if row["through_week"] <= 22]
    late_roll = [row for row in rolled if row["through_week"] >= 32]
    assert early_roll[-1]["gap"] > late_roll[-1]["gap"] + 0.10


def test_one_recent_week_temporarily_resembles_the_old_pattern(weekly):
    decoy = next(row for row in weekly if row["week"] == DECOY_WEEK)
    neighbours = [
        row
        for row in weekly
        if 29 <= row["week"] <= 36 and row["week"] != DECOY_WEEK
    ]
    neighbour_gap = sum(row["gap"] for row in neighbours) / len(neighbours)
    assert decoy["morning_rate"] >= 0.68
    assert decoy["afternoon_rate"] <= 0.58
    assert decoy["gap"] >= 0.15
    assert decoy["morning_n"] >= 8
    assert decoy["afternoon_n"] >= 8
    assert neighbour_gap < 0.10
    assert decoy["gap"] > neighbour_gap + 0.12
    assert sum(row["gap"] < 0.16 for row in neighbours) >= 4


def test_interruptions_remain_neutral(rows):
    interrupted = _rate(rows, lambda row: row["interrupted"])
    uninterrupted = _rate(rows, lambda row: not row["interrupted"])
    assert abs(interrupted - uninterrupted) <= 0.12

    rates = []
    for first, last in ((1, 22), (23, 28), (29, 36)):
        matched = rows_for_weeks(rows, START_DATE, first, last)
        assert matched
        rates.append(
            sum(row["interrupted"] for row in matched) / len(matched)
        )
    assert max(rates) - min(rates) <= 0.08


def test_categories_remain_neutral(rows):
    rates = []
    for category in {parse_task_category(row["task"].title) for row in rows}:
        matched = [
            row
            for row in rows
            if parse_task_category(row["task"].title) == category
        ]
        if len(matched) < 40:
            continue
        rates.append(sum(row["positive"] for row in matched) / len(matched))
    assert rates
    assert max(rates) - min(rates) <= 0.16


def test_workload_remains_broadly_stable(eval_db):
    weeks = weekly_workload_rows(eval_db, START_DATE)
    historical = [row for row in weeks if row["week"] <= 22]
    transition = [row for row in weeks if 23 <= row["week"] <= 28]
    recent = [row for row in weeks if row["week"] >= 29]

    def mean_planned(chunk):
        return sum(row["planned_sessions"] for row in chunk) / len(chunk)

    historical_planned = mean_planned(historical)
    recent_planned = mean_planned(recent)
    transition_planned = mean_planned(transition)
    assert abs(historical_planned - recent_planned) <= 18
    assert abs(transition_planned - historical_planned) <= 20
    historical_ratio = sum(
        row["actual_sessions"] for row in historical
    ) / max(1, sum(row["planned_sessions"] for row in historical))
    recent_ratio = sum(
        row["actual_sessions"] for row in recent
    ) / max(1, sum(row["planned_sessions"] for row in recent))
    assert abs(historical_ratio - recent_ratio) <= 0.12
    assert all(
        row["planned_sessions"] >= 8
        for row in weeks
        if row["week"] >= 29
    )


def test_no_task_age_abandonment_gradient(eval_db):
    records = terminal_task_records(eval_db)
    young = [row for row in records if row["execution_age_days"] <= 7]
    old = [row for row in records if row["execution_age_days"] >= 15]
    assert young and old
    young_rate = sum(row["abandoned"] for row in young) / len(young)
    old_rate = sum(row["abandoned"] for row in old) / len(old)
    assert abs(old_rate - young_rate) <= 0.22
    assert old_rate < 0.45


def test_no_monday_morning_effect(rows):
    weekday_rates = [
        _rate(rows, lambda row, d=weekday: row["weekday"] == d)
        for weekday in range(5)
    ]
    monday_morning = _rate(
        rows,
        lambda row: row["weekday"] == 0 and row["period"] == "morning",
    )
    other_morning = _rate(
        rows,
        lambda row: row["weekday"] != 0 and row["period"] == "morning",
    )
    assert max(weekday_rates) - min(weekday_rates) <= 0.18
    assert abs(monday_morning - other_morning) <= 0.14


def test_morning_afternoon_mix_is_spread_across_weekdays(rows):
    for period in ("morning", "afternoon"):
        weekdays = {
            row["weekday"]
            for row in rows
            if row["period"] == period
        }
        assert weekdays == {0, 1, 2, 3, 4}
