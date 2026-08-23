import inspect
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app.analytics.change import (
    compare_morning_afternoon_windows,
    morning_afternoon_window,
    rolling_window_dates,
    weekly_morning_afternoon_outcomes,
)
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
    assert payload["agent_evidence_contract"] == "change-v1"
    generator_truth = payload["generator_truth"]
    assert "historical_morning_advantage_is_strong" in (
        generator_truth
    )
    assert "lifetime_average_overstates_current_morning_advantage" in (
        generator_truth
    )
    assert "expected_patterns" not in payload
    assert "no_meaningful_weekday_effect" in (
        payload["expected_non_patterns"]
    )
    assert "no_workload_regime_explaining_the_change" in (
        payload["expected_non_patterns"]
    )


def test_agent_evaluable_ground_truth_matches_evidence_contract():
    payload = yaml.safe_load(GROUND_TRUTH_PATH.read_text())
    agent_patterns = payload["agent_expected_patterns"]
    agent_hypotheses = payload["agent_expected_hypotheses"]
    agent_insufficient = payload["agent_expected_insufficient_evidence"]
    able_blob = " ".join(payload["agent_should_be_able_to_say"]).lower()
    must_blob = " ".join(payload["agent_should_not_claim"]).lower()
    hidden = set(payload["not_agent_evaluable"])

    assert "full_period_still_shows_a_morning_advantage" in (
        agent_patterns
    )
    assert (
        "full_period_relationship_is_not_representative_of_recent_behaviour"
        in agent_patterns
    )
    assert (
        "recent_windows_show_little_or_no_meaningful_morning_advantage"
        in agent_patterns
    )
    assert (
        "morning_afternoon_gap_has_materially_weakened"
        in agent_patterns
    )
    assert (
        "evidence_supports_change_not_necessarily_a_stable_reversal"
        in agent_patterns
    )
    assert hidden.isdisjoint(agent_patterns)
    assert hidden.isdisjoint(agent_hypotheses)
    assert "generator_phase_labels" in hidden
    assert "hidden_decoy_week_identities" in hidden
    assert "exact_planted_transition_week" in hidden
    assert "full-period data still shows a morning advantage" in (
        able_blob
    )
    assert "not representative of the most recent behaviour" in (
        able_blob
    )
    assert "little or no meaningful morning advantage" in able_blob
    assert "preceding comparable window had a much larger" in able_blob
    assert "materially weakened" in able_blob
    assert "not necessarily a stable reversal" in able_blob
    assert "new persistent regimes" in able_blob
    assert "does not establish why the change occurred" in able_blob
    assert "afternoon is now definitively better" in must_blob
    assert "one recent strong week establishes a new morning" in (
        must_blob
    )
    assert "old lifetime pattern should still be used" in must_blob
    assert "precise change-point date" in must_blob
    assert "afternoon_is_not_shown_to_be_definitively_better" in (
        agent_insufficient
    )
    assert "evidence_does_not_establish_why_the_change_occurred" in (
        agent_insufficient
    )
    assert "no_precise_change_point_date_is_supplied" in (
        agent_insufficient
    )
    assert "changed_work_mix_may_explain_the_shift" in (
        agent_hypotheses
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


def _week_span(first: int, last: int) -> tuple[date, date]:
    start = START_DATE + timedelta(weeks=first - 1)
    end = START_DATE + timedelta(weeks=last) - timedelta(days=1)
    return start, end


def test_analytics_recovers_dataset_e_window_progression(
    generated_eval,
    eval_db,
):
    result = generated_eval["result"]
    full = morning_afternoon_window(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    historical = morning_afternoon_window(
        eval_db,
        from_date=_week_span(*HISTORICAL_WEEKS)[0],
        to_date=_week_span(*HISTORICAL_WEEKS)[1],
    )
    transition = morning_afternoon_window(
        eval_db,
        from_date=_week_span(*TRANSITION_WEEKS)[0],
        to_date=_week_span(*TRANSITION_WEEKS)[1],
    )
    recent_from, recent_to = rolling_window_dates(
        result.end_date,
        weeks=8,
    )
    recent = morning_afternoon_window(
        eval_db,
        from_date=recent_from,
        to_date=recent_to,
    )
    recent16_from, recent16_to = rolling_window_dates(
        result.end_date,
        weeks=16,
    )
    preceding_to = recent16_from - timedelta(days=1)
    preceding_from, preceding_to = rolling_window_dates(
        preceding_to,
        weeks=16,
    )
    compared = compare_morning_afternoon_windows(
        eval_db,
        current_from_date=recent16_from,
        current_to_date=recent16_to,
        baseline_from_date=preceding_from,
        baseline_to_date=preceding_to,
    )

    assert full.positive_rate_gap >= 0.08
    assert 0.64 <= full.morning_positive_rate <= 0.78
    assert historical.positive_rate_gap >= 0.18
    assert historical.morning_positive_rate > historical.afternoon_positive_rate
    assert abs(recent.positive_rate_gap) <= 0.14
    assert recent.positive_rate_gap <= 0.10
    assert transition.positive_rate_gap < historical.positive_rate_gap - 0.04
    assert compared.baseline.positive_rate_gap >= 0.10
    assert compared.current.positive_rate_gap < (
        compared.baseline.positive_rate_gap - 0.08
    )
    assert compared.gap_change < 0
    forbidden = {
        "behaviour_changed",
        "improved",
        "declined",
        "regime",
        "phase",
        "recommendation",
        "confidence",
        "significance",
    }
    assert forbidden.isdisjoint(full.__dataclass_fields__)
    assert forbidden.isdisjoint(compared.__dataclass_fields__)


def test_analytics_exposes_dataset_e_weekly_series(
    generated_eval,
    eval_db,
):
    result = generated_eval["result"]
    weeks = weekly_morning_afternoon_outcomes(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    by_start = {
        group.week_start_date: group for group in weeks.groups
    }
    historical_starts = [
        START_DATE + timedelta(weeks=week - 1)
        for week in range(HISTORICAL_WEEKS[0], HISTORICAL_WEEKS[1] + 1)
    ]
    recent_starts = [
        START_DATE + timedelta(weeks=week - 1)
        for week in range(RECENT_WEEKS[0], RECENT_WEEKS[1] + 1)
        if week != DECOY_WEEK
    ]
    early_positive = [
        by_start[start]
        for start in historical_starts
        if start in by_start
        and by_start[start].positive_rate_gap is not None
        and by_start[start].positive_rate_gap >= 0.10
    ]
    recent_mixed = [
        by_start[start]
        for start in recent_starts
        if start in by_start
        and by_start[start].positive_rate_gap is not None
        and by_start[start].positive_rate_gap < 0.18
    ]
    decoy = by_start[START_DATE + timedelta(weeks=DECOY_WEEK - 1)]
    assert len(early_positive) >= 12
    assert len(recent_mixed) >= 4
    assert decoy.positive_rate_gap >= 0.15
    assert decoy.morning_positive_rate >= 0.68
    assert "anomalous" not in decoy.__dataclass_fields__
    assert "misleading" not in weeks.__dataclass_fields__


def test_agent_change_evidence_hides_scenario_and_phase_labels(
    generated_eval,
    eval_db,
):
    import json
    from datetime import timedelta

    from evaluation.agent.evidence import (
        build_change_evidence,
        evidence_ids,
        serialize_evidence,
    )
    from evaluation.agent.prompt import render_prompt
    from evaluation.agent.runner import run_case

    result = generated_eval["result"]
    production = morning_afternoon_window(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    recent16_from, recent16_to = rolling_window_dates(
        result.end_date,
        weeks=16,
    )
    preceding_from, preceding_to = rolling_window_dates(
        recent16_from - timedelta(days=1),
        weeks=16,
    )
    weeks = weekly_morning_afternoon_outcomes(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
    )
    evidence = build_change_evidence(
        eval_db,
        from_date=result.start_date,
        to_date=result.end_date,
        case_id="case_e",
    )
    assert evidence["case_id"] == "case_e"
    assert evidence["full_period"]["morning_n"] == (
        production.morning_session_count
    )
    assert evidence["full_period"]["positive_rate_gap"] == (
        production.positive_rate_gap
    )
    recent16 = next(
        row
        for row in evidence["recent_windows"]
        if row["id"] == "window:recent-16w"
    )
    assert recent16["from"] == recent16_from.isoformat()
    assert recent16["to"] == recent16_to.isoformat()
    preceding = evidence["preceding_windows"][0]
    assert preceding["from"] == preceding_from.isoformat()
    assert preceding["to"] == preceding_to.isoformat()
    assert [
        row["week_start"] for row in evidence["weekly_morning_afternoon"]
    ] == [group.week_start_date.isoformat() for group in weeks.groups]
    blob = serialize_evidence(evidence)
    prompt = render_prompt(
        evidence,
        version="change-v1",
    ).combined_text()
    for token in (
        "behaviour_change",
        "expected_patterns",
        "generator_truth",
        "historical_phase",
        "transition_phase",
        "recent_phase",
        "decoy_week",
        "decoy",
        "ground_truth",
        "dataset e",
    ):
        assert token not in blob.lower()
        assert token not in prompt.lower()
    known = evidence_ids(evidence)
    assert "window:full" in known
    assert "comparison:recent16-vs-preceding16" in known
    assert evidence["weekly_morning_afternoon"][0]["id"] in known
    record = run_case(
        eval_db,
        case_id="case_e",
        from_date=result.start_date,
        to_date=result.end_date,
        dry_run=True,
        model=None,
        prompt_version="change-v1",
    )
    assert record["case_id"] == "case_e"
    persisted = json.dumps(record["prompt"]) + json.dumps(
        record["evidence"]
    )
    assert "behaviour_change" not in persisted.lower()
    assert "decoy" not in persisted.lower()
    assert "historical_phase" not in persisted.lower()

