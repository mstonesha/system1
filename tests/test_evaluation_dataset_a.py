import inspect
import os
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import parse_task_category
from evaluation.clock import classify_day_part, working_days
from evaluation.database import EVAL_DATABASE_NAME, reset_eval_schema
from evaluation.export import export_evaluation_csv
from evaluation.generate import main
from evaluation.scenarios import temporal_patterns as scenario
from evaluation.scenarios.temporal_patterns import (
    DEFAULT_SEED,
    START_DATE,
    WORKING_WEEKS,
    generate_temporal_patterns,
)


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


def _server_url():
    return make_url(os.environ["DATABASE_URL"])


def _eval_url():
    return _server_url().set(database=EVAL_DATABASE_NAME)


def _admin_engine() -> Engine:
    return create_engine(
        _server_url().set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )


def _drop_eval_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :name "
                "AND pid <> pg_backend_pid()"
            ),
            {"name": EVAL_DATABASE_NAME},
        )
        connection.execute(
            text(
                f'DROP DATABASE IF EXISTS "{EVAL_DATABASE_NAME}"'
            )
        )


def _create_eval_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                f'CREATE DATABASE "{EVAL_DATABASE_NAME}"'
            )
        )


def _snapshot(db: Session) -> tuple:
    tasks = [
        (
            task.title,
            task.status,
            task.parent_task_id,
            task.priority,
            task.estimated_sessions,
            (
                task.completed_at.isoformat()
                if task.completed_at
                else None
            ),
            task.created_at.isoformat(),
        )
        for task in db.query(Task).order_by(Task.id)
    ]
    daily_tasks = [
        (
            daily.task_id,
            daily.date.isoformat(),
            daily.planned_sessions,
            daily.state,
            daily.sort_order,
        )
        for daily in db.query(DailyTask).order_by(DailyTask.id)
    ]
    sessions = [
        (
            work.daily_task_id,
            work.started_at.isoformat(),
            work.ended_at.isoformat() if work.ended_at else None,
            work.outcome,
            work.interrupted,
            work.session_state,
            work.actual_duration_seconds,
        )
        for work in db.query(WorkSession).order_by(WorkSession.id)
    ]
    return (tasks, daily_tasks, sessions)


def _generate(engine: Engine, seed: int = DEFAULT_SEED):
    reset_eval_schema(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    with SessionLocal() as db:
        result = generate_temporal_patterns(
            db,
            seed=seed,
            timezone_name=get_settings().timezone,
        )
        db.commit()
        snap = _snapshot(db)
    return result, snap


@pytest.fixture(scope="module")
def generated_eval():
    admin = _admin_engine()
    engine = None
    try:
        _drop_eval_database(admin)
        _create_eval_database(admin)
        engine = create_engine(_eval_url())
        first, first_snap = _generate(engine)
        second, second_snap = _generate(engine)
        SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )
        yield {
            "engine": engine,
            "result": second,
            "first_snap": first_snap,
            "second_snap": second_snap,
            "first_result": first,
            "SessionLocal": SessionLocal,
        }
    finally:
        if engine is not None:
            engine.dispose()
        _drop_eval_database(admin)
        admin.dispose()


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
    assert "mornings_have_highest_positive_outcome_rate_overall" in (
        payload["expected_patterns"]
    )
    assert "monday_morning_is_a_strong_negative_exception" in (
        payload["expected_patterns"]
    )


def test_generator_does_not_load_ground_truth():
    generate_source = inspect.getsource(main)
    scenario_source = inspect.getsource(scenario)

    assert "temporal_patterns.yaml" not in generate_source
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
    assert version == "f1a9b3c4d5e6"


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
