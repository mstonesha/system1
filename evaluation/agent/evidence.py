"""Deterministic temporal evidence for evaluation cases A and F.

Calls production analytics only. Does not load ground truth, does
not name scenarios, and does not narrate the measurements.
temporal-v1 omits weekly series so the original contract stays
reproducible. temporal-v2 adds weekly morning/afternoon rows.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.analytics.change import (
    morning_afternoon_window,
    weekly_morning_afternoon_outcomes,
)
from app.analytics.outcomes import (
    session_outcomes_by_daypart,
    session_outcomes_by_weekday,
    session_outcomes_by_weekday_daypart,
)


ALLOWED_CASE_IDS = frozenset({"case_a", "case_f"})
EVIDENCE_CONTRACTS = frozenset({"temporal-v1", "temporal-v2"})
DEFAULT_EVIDENCE_CONTRACT = "temporal-v1"


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
    if case_id not in ALLOWED_CASE_IDS:
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
    if version == "temporal-v2":
        weeks = weekly_morning_afternoon_outcomes(
            db,
            from_date=from_date,
            to_date=to_date,
        )
        package["weekly_morning_afternoon"] = [
            _weekly_row(group) for group in weeks.groups
        ]
    return package


def evidence_ids(package: dict) -> set[str]:
    """Return citeable ids present in a temporal evidence package."""
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
