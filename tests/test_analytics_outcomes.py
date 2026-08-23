import ast
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.analytics.dayparts import (
    DAYPART_NAMES,
    classify_daypart,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)
from app.analytics.types import (
    COMMITTED_OUTCOMES,
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
)
from app.models import WorkSession
from app.services.sessions import ALLOWED_OUTCOMES
from app.time import UTC

LONDON = ZoneInfo("Europe/London")
ANALYTICS_DIR = (
    Path(__file__).resolve().parents[1] / "app" / "analytics"
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
    session_state="completed",
):
    task = make_task()
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
        session_state=session_state,
        outcome=outcome,
    )
    db.add(work_session)
    db.commit()
    db.refresh(work_session)
    return work_session


def _assert_group_reconciles(group):
    assert (
        group.progress_count
        + group.complete_count
        + group.stuck_count
        + group.paused_count
        + group.abandoned_count
        == group.session_count
    )
    assert (
        group.positive_count + group.negative_count
        == group.session_count
    )
    assert group.positive_count == (
        group.progress_count + group.complete_count
    )
    assert group.negative_count == (
        group.stuck_count
        + group.paused_count
        + group.abandoned_count
    )
    if group.session_count > 0:
        assert group.positive_rate == (
            group.positive_count / group.session_count
        )
        assert group.negative_rate == (
            group.negative_count / group.session_count
        )
        assert (
            abs(
                group.positive_rate
                + group.negative_rate
                - 1.0
            )
            < 1e-12
        )


def test_committed_outcomes_match_session_service():
    assert set(COMMITTED_OUTCOMES) == ALLOWED_OUTCOMES
    assert POSITIVE_OUTCOMES.isdisjoint(NEGATIVE_OUTCOMES)
    assert POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES == (
        ALLOWED_OUTCOMES
    )


def test_analytics_package_does_not_import_evaluation():
    for path in ANALYTICS_DIR.glob("*.py"):
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


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (0, 0, "overnight"),
        (5, 59, "overnight"),
        (6, 0, "early_morning"),
        (8, 59, "early_morning"),
        (9, 0, "morning"),
        (11, 59, "morning"),
        (12, 0, "early_afternoon"),
        (14, 59, "early_afternoon"),
        (15, 0, "late_afternoon"),
        (17, 59, "late_afternoon"),
        (18, 0, "evening"),
        (23, 59, "evening"),
    ],
)
def test_classify_daypart_boundaries(hour, minute, expected):
    local = _local(2026, 1, 15, hour, minute)
    assert classify_daypart(local) == expected


def test_empty_period_returns_empty_results(db):
    from_date = date(2020, 1, 6)
    to_date = date(2020, 1, 10)
    weekday = session_outcomes_by_weekday(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    daypart = session_outcomes_by_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    interaction = session_outcomes_by_weekday_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )

    for result in (weekday, daypart, interaction):
        assert result.from_date == from_date
        assert result.to_date == to_date
        assert result.timezone == "Europe/London"
        assert result.total_sessions == 0
        assert result.groups == ()


def test_inverted_period_is_rejected(db):
    with pytest.raises(ValueError, match="from_date"):
        session_outcomes_by_weekday(
            db,
            from_date=date(2026, 1, 10),
            to_date=date(2026, 1, 9),
        )


def test_exact_local_date_filtering(
    db,
    make_task,
    make_daily_task,
):
    before = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 14, 23, 59),
        outcome="stuck",
    )
    included = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 0, 0),
        outcome="progress",
    )
    last = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 16, 23, 59),
        outcome="complete",
    )
    after = _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 17, 0, 0),
        outcome="paused",
    )

    result = session_outcomes_by_weekday(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 16),
    )

    assert result.total_sessions == 2
    assert {before.id, after.id}.isdisjoint(
        {included.id, last.id}
    )
    by_day = {
        group.weekday: group for group in result.groups
    }
    assert by_day["Thursday"].session_count == 1
    assert by_day["Thursday"].progress_count == 1
    assert by_day["Friday"].session_count == 1
    assert by_day["Friday"].complete_count == 1


def test_incomplete_and_uncommitted_sessions_are_excluded(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 10, 0),
        outcome="progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 11, 0),
        outcome=None,
        session_state="completed",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 12, 0),
        outcome=None,
        session_state="running",
    )

    result = session_outcomes_by_daypart(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )

    assert result.total_sessions == 1
    assert result.groups[0].daypart == "morning"
    assert result.groups[0].progress_count == 1


def test_counts_and_rates_reconcile(
    db,
    make_task,
    make_daily_task,
):
    outcomes = (
        "progress",
        "progress",
        "complete",
        "stuck",
        "paused",
        "abandoned",
    )
    for index, outcome in enumerate(outcomes):
        _add_completed(
            db,
            make_task,
            make_daily_task,
            _local(2026, 1, 15, 10, index),
            outcome=outcome,
        )

    result = session_outcomes_by_weekday(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )

    assert result.total_sessions == 6
    assert len(result.groups) == 1
    group = result.groups[0]
    _assert_group_reconciles(group)
    assert group.weekday == "Thursday"
    assert group.weekday_number == 4
    assert group.progress_count == 2
    assert group.complete_count == 1
    assert group.stuck_count == 1
    assert group.paused_count == 1
    assert group.abandoned_count == 1
    assert group.positive_count == 3
    assert group.negative_count == 3
    assert group.positive_rate == 0.5
    assert group.negative_rate == 0.5


def test_weekday_groups_use_stable_calendar_order(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 16, 10, 0),
        outcome="complete",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 10, 0),
        outcome="stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 14, 10, 0),
        outcome="progress",
    )

    result = session_outcomes_by_weekday(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
    )

    assert [group.weekday for group in result.groups] == [
        "Monday",
        "Wednesday",
        "Friday",
    ]
    assert [group.weekday_number for group in result.groups] == [
        1,
        3,
        5,
    ]


def test_overnight_sessions_are_kept_not_dropped(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 5, 30),
        outcome="stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 10, 0),
        outcome="progress",
    )

    result = session_outcomes_by_daypart(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    by_part = {
        group.daypart: group for group in result.groups
    }

    assert result.total_sessions == 2
    assert [group.daypart for group in result.groups] == [
        "overnight",
        "morning",
    ]
    assert by_part["overnight"].session_count == 1
    assert by_part["overnight"].stuck_count == 1
    assert "overnight" in DAYPART_NAMES


def test_bst_utc_midnight_groups_into_local_weekday_and_daypart(
    db,
    make_task,
    make_daily_task,
):
    # 2026-07-14 23:30 UTC is Wednesday 00:30 BST.
    utc_near_midnight = datetime(
        2026,
        7,
        14,
        23,
        30,
        tzinfo=UTC,
    )
    local = utc_near_midnight.astimezone(LONDON)
    assert local.date() == date(2026, 7, 15)
    assert local.weekday() == 2
    assert local.time() == time(0, 30)
    assert utc_near_midnight.date() == date(2026, 7, 14)

    _add_completed(
        db,
        make_task,
        make_daily_task,
        local,
        outcome="progress",
    )

    included = session_outcomes_by_weekday_daypart(
        db,
        from_date=date(2026, 7, 15),
        to_date=date(2026, 7, 15),
    )
    previous = session_outcomes_by_weekday(
        db,
        from_date=date(2026, 7, 14),
        to_date=date(2026, 7, 14),
    )
    daypart = session_outcomes_by_daypart(
        db,
        from_date=date(2026, 7, 15),
        to_date=date(2026, 7, 15),
    )

    assert previous.total_sessions == 0
    assert included.total_sessions == 1
    cell = included.groups[0]
    assert cell.weekday == "Wednesday"
    assert cell.weekday_number == 3
    assert cell.daypart == "overnight"
    assert daypart.groups[0].daypart == "overnight"


def test_zero_count_interaction_cells_are_omitted(
    db,
    make_task,
    make_daily_task,
):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 15, 10, 0),
        outcome="progress",
    )

    result = session_outcomes_by_weekday_daypart(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )

    assert len(result.groups) == 1
    assert result.groups[0].weekday == "Thursday"
    assert result.groups[0].daypart == "morning"


def test_result_has_no_interpretive_fields(db):
    result = session_outcomes_by_weekday(
        db,
        from_date=date(2026, 1, 15),
        to_date=date(2026, 1, 15),
    )
    forbidden = {
        "best_day",
        "confidence",
        "significance",
        "recommendation",
        "pattern_detected",
    }
    assert forbidden.isdisjoint(result.__dataclass_fields__)
    assert forbidden.isdisjoint(dir(result))
