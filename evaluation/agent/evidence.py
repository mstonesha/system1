"""Deterministic evidence packages for evaluation cases.

Calls production analytics only. Does not load ground truth, does
not name scenarios, and does not narrate the measurements.
Does not parse or classify task titles.

temporal-v1 omits weekly series so the original contract stays
reproducible. temporal-v2 adds weekly morning/afternoon rows.
temporal-v3 reuses the temporal-v2 evidence package unchanged.
dependencies-v1 uses interruption outcomes and the bounded stuck
task drilldown. task-age-v1 uses execution-age abandonment
buckets and datable terminal-task rows.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.analytics.change import (
    morning_afternoon_window,
    weekly_morning_afternoon_outcomes,
)
from app.analytics.drilldown import (
    DEFAULT_LIMIT as STUCK_DRILLDOWN_LIMIT,
    stuck_task_drilldown,
)
from app.analytics.task_age import (
    classify_execution_age_days,
    task_abandonment_by_execution_age,
    terminal_tasks_with_execution_age,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_interruption,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)


ALLOWED_TEMPORAL_CASE_IDS = frozenset({"case_a", "case_f"})
ALLOWED_DEPENDENCY_CASE_IDS = frozenset({"case_b"})
ALLOWED_TASK_AGE_CASE_IDS = frozenset({"case_c"})
ALLOWED_CASE_IDS = (
    ALLOWED_TEMPORAL_CASE_IDS
    | ALLOWED_DEPENDENCY_CASE_IDS
    | ALLOWED_TASK_AGE_CASE_IDS
)
EVIDENCE_CONTRACTS = frozenset(
    {"temporal-v1", "temporal-v2", "temporal-v3"}
)
DEPENDENCY_CONTRACTS = frozenset({"dependencies-v1"})
TASK_AGE_CONTRACTS = frozenset({"task-age-v1"})
DEFAULT_EVIDENCE_CONTRACT = "temporal-v1"
WEEKLY_EVIDENCE_VERSIONS = frozenset(
    {"temporal-v2", "temporal-v3"}
)


def build_temporal_evidence(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    case_id: str,
    version: str = DEFAULT_EVIDENCE_CONTRACT,
) -> dict:
    """Assemble a temporal evidence package.

    ``case_id`` must be an opaque evaluation identifier, not a
    scenario name. ``version`` selects the evidence contract.
    """
    if case_id not in ALLOWED_TEMPORAL_CASE_IDS:
        raise ValueError(
            "case_id must be an opaque evaluation identifier "
            f"(case_a or case_f); got {case_id!r}."
        )
    if version not in EVIDENCE_CONTRACTS:
        raise ValueError(
            "Unknown evidence contract "
            f"{version!r}. Expected one of "
            + ", ".join(sorted(EVIDENCE_CONTRACTS))
        )

    weekdays = session_outcomes_by_weekday(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    dayparts = session_outcomes_by_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    cells = session_outcomes_by_weekday_daypart(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    window = morning_afternoon_window(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    package = {
        "case_id": case_id,
        "period": {
            "from": weekdays.from_date.isoformat(),
            "to": weekdays.to_date.isoformat(),
            "timezone": weekdays.timezone,
            "total_sessions": weekdays.total_sessions,
        },
        "weekday_outcomes": [
            {
                "id": _weekday_id(group.weekday),
                "weekday": group.weekday,
                **_outcome_counts(group),
            }
            for group in weekdays.groups
        ],
        "daypart_outcomes": [
            {
                "id": _daypart_id(group.daypart),
                "daypart": group.daypart,
                **_outcome_counts(group),
            }
            for group in dayparts.groups
        ],
        "weekday_daypart_outcomes": [
            {
                "id": _weekday_daypart_id(
                    group.weekday,
                    group.daypart,
                ),
                "weekday": group.weekday,
                "daypart": group.daypart,
                **_outcome_counts(group),
            }
            for group in cells.groups
        ],
        "morning_afternoon": {
            "id": "morning_afternoon",
            "morning": {
                "id": "morning_afternoon:morning",
                "n": window.morning_session_count,
                "positive_count": window.morning_positive_count,
                "negative_count": window.morning_negative_count,
                "positive_rate": window.morning_positive_rate,
            },
            "afternoon": {
                "id": "morning_afternoon:afternoon",
                "n": window.afternoon_session_count,
                "positive_count": window.afternoon_positive_count,
                "negative_count": window.afternoon_negative_count,
                "positive_rate": window.afternoon_positive_rate,
            },
            "positive_rate_gap": window.positive_rate_gap,
        },
    }
    if version in WEEKLY_EVIDENCE_VERSIONS:
        weeks = weekly_morning_afternoon_outcomes(
            db,
            from_date=from_date,
            to_date=to_date,
        )
        package["weekly_morning_afternoon"] = [
            _weekly_row(group) for group in weeks.groups
        ]
    return package


def build_dependencies_evidence(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    case_id: str,
    version: str = "dependencies-v1",
) -> dict:
    """Assemble interruption and bounded stuck-task evidence.

    Titles are passed through exactly as production returns them.
    This builder does not parse or classify titles.
    """
    if case_id not in ALLOWED_DEPENDENCY_CASE_IDS:
        raise ValueError(
            "case_id must be an opaque evaluation identifier "
            f"(case_b); got {case_id!r}."
        )
    if version not in DEPENDENCY_CONTRACTS:
        raise ValueError(
            "Unknown evidence contract "
            f"{version!r}. Expected one of "
            + ", ".join(sorted(DEPENDENCY_CONTRACTS))
        )
    interruptions = session_outcomes_by_interruption(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    drilldown = stuck_task_drilldown(
        db,
        from_date=from_date,
        to_date=to_date,
        limit=STUCK_DRILLDOWN_LIMIT,
    )
    return {
        "case_id": case_id,
        "period": {
            "from": interruptions.from_date.isoformat(),
            "to": interruptions.to_date.isoformat(),
            "timezone": interruptions.timezone,
            "total_sessions": interruptions.total_sessions,
        },
        "interruption_outcomes": [
            {
                "id": _interruption_id(group.interrupted),
                "interrupted": group.interrupted,
                **_outcome_counts(group),
            }
            for group in interruptions.groups
        ],
        "stuck_drilldown": {
            "id": "stuck_drilldown",
            "total_stuck_sessions": drilldown.total_stuck_sessions,
            "total_distinct_stuck_tasks": (
                drilldown.total_distinct_stuck_tasks
            ),
            "returned_task_count": drilldown.returned_task_count,
            "limit": drilldown.limit,
            "tasks": [
                {
                    "id": _stuck_task_id(row.task_id),
                    "task_id": row.task_id,
                    "title": row.title,
                    "stuck_session_count": row.stuck_session_count,
                    "first_stuck_date": (
                        row.first_stuck_date.isoformat()
                    ),
                    "last_stuck_date": (
                        row.last_stuck_date.isoformat()
                    ),
                    "task_status": row.task_status,
                }
                for row in drilldown.tasks
            ],
        },
    }


def build_task_age_evidence(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    case_id: str,
    version: str = "task-age-v1",
) -> dict:
    """Assemble execution-age abandonment evidence.

    Uses production age calculation only. Does not compute an
    alternative age from Task record creation and does not
    include titles.
    """
    if case_id not in ALLOWED_TASK_AGE_CASE_IDS:
        raise ValueError(
            "case_id must be an opaque evaluation identifier "
            f"(case_c); got {case_id!r}."
        )
    if version not in TASK_AGE_CONTRACTS:
        raise ValueError(
            "Unknown evidence contract "
            f"{version!r}. Expected one of "
            + ", ".join(sorted(TASK_AGE_CONTRACTS))
        )
    analysis = task_abandonment_by_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    details = terminal_tasks_with_execution_age(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return {
        "case_id": case_id,
        "period": {
            "from": analysis.from_date.isoformat(),
            "to": analysis.to_date.isoformat(),
            "timezone": analysis.timezone,
            "total_terminal_tasks": analysis.total_terminal_tasks,
        },
        "execution_age_abandonment": [
            {
                "id": _execution_age_id(group.age_bucket),
                "bucket": group.age_bucket,
                "n": group.terminal_task_count,
                "completed_count": group.completed_count,
                "abandoned_count": group.abandoned_count,
                "abandonment_rate": group.abandonment_rate,
            }
            for group in analysis.groups
        ],
        "terminal_task_drilldown": {
            "id": "terminal_task_drilldown",
            "returned_task_count": len(details),
            "tasks": [
                {
                    "id": _terminal_task_id(row.task_id),
                    "task_id": row.task_id,
                    "execution_age_days": row.execution_age_days,
                    "execution_age_bucket": (
                        classify_execution_age_days(
                            row.execution_age_days
                        )
                    ),
                    "terminal_outcome": row.terminal_outcome,
                }
                for row in details
            ],
        },
    }


def build_evidence(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    case_id: str,
    version: str,
) -> dict:
    """Dispatch to the evidence builder for a prompt contract."""
    if version in EVIDENCE_CONTRACTS:
        return build_temporal_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id=case_id,
            version=version,
        )
    if version in DEPENDENCY_CONTRACTS:
        return build_dependencies_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id=case_id,
            version=version,
        )
    if version in TASK_AGE_CONTRACTS:
        return build_task_age_evidence(
            db,
            from_date=from_date,
            to_date=to_date,
            case_id=case_id,
            version=version,
        )
    raise ValueError(
        f"Unknown evidence contract {version!r}."
    )


def evidence_ids(package: dict) -> set[str]:
    """Return citeable ids present in an evidence package."""
    ids: set[str] = set()
    for row in package.get("weekday_outcomes", ()):
        ids.add(row["id"])
    for row in package.get("daypart_outcomes", ()):
        ids.add(row["id"])
    for row in package.get("weekday_daypart_outcomes", ()):
        ids.add(row["id"])
    for row in package.get("weekly_morning_afternoon", ()):
        ids.add(row["id"])
    window = package.get("morning_afternoon") or {}
    if "id" in window:
        ids.add(window["id"])
    morning = window.get("morning") or {}
    afternoon = window.get("afternoon") or {}
    if "id" in morning:
        ids.add(morning["id"])
    if "id" in afternoon:
        ids.add(afternoon["id"])
    for row in package.get("interruption_outcomes", ()):
        ids.add(row["id"])
    drilldown = package.get("stuck_drilldown") or {}
    if "id" in drilldown:
        ids.add(drilldown["id"])
    for row in drilldown.get("tasks") or ():
        ids.add(row["id"])
    for row in package.get("execution_age_abandonment", ()):
        ids.add(row["id"])
    terminal = package.get("terminal_task_drilldown") or {}
    if "id" in terminal:
        ids.add(terminal["id"])
    for row in terminal.get("tasks") or ():
        ids.add(row["id"])
    return ids


def serialize_evidence(package: dict) -> str:
    """Compact deterministic JSON for the model prompt."""
    return _dumps(package)


def _weekly_row(group) -> dict:
    week_start = group.week_start_date.isoformat()
    return {
        "id": f"week:{week_start}",
        "week_start": week_start,
        "morning_n": group.morning_session_count,
        "morning_positive_rate": group.morning_positive_rate,
        "afternoon_n": group.afternoon_session_count,
        "afternoon_positive_rate": group.afternoon_positive_rate,
        "positive_rate_gap": group.positive_rate_gap,
    }


def _outcome_counts(group) -> dict:
    return {
        "n": group.session_count,
        "progress": group.progress_count,
        "complete": group.complete_count,
        "stuck": group.stuck_count,
        "paused": group.paused_count,
        "abandoned": group.abandoned_count,
        "positive_count": group.positive_count,
        "negative_count": group.negative_count,
        "positive_rate": group.positive_rate,
    }


def _interruption_id(interrupted: bool) -> str:
    return f"interruption:{'true' if interrupted else 'false'}"


def _stuck_task_id(task_id: int) -> str:
    return f"stuck_task:{int(task_id)}"


def _execution_age_id(bucket: str) -> str:
    return f"execution_age:{bucket}"


def _terminal_task_id(task_id: int) -> str:
    return f"terminal_task:{int(task_id)}"


def _weekday_id(weekday: str) -> str:
    return f"weekday:{weekday.lower()}"


def _daypart_id(daypart: str) -> str:
    return f"daypart:{daypart}"


def _weekday_daypart_id(weekday: str, daypart: str) -> str:
    return f"weekday_daypart:{weekday.lower()}:{daypart}"


def _dumps(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
