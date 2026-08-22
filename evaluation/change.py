"""Morning/afternoon window diagnostics for evaluation datasets.

These helpers derive recency comparisons from existing WorkSession
timestamps. They do not add production fields and must not load
ground-truth YAML.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.clock import classify_day_part
from evaluation.planning import scenario_week_index, week_start_date


POSITIVE_OUTCOMES = frozenset({"progress", "complete"})
MORNING_PARTS = frozenset({"morning"})
AFTERNOON_PARTS = frozenset({"early_afternoon", "late_afternoon"})


def classify_focus_period(day_part: str) -> str:
    if day_part in MORNING_PARTS:
        return "morning"
    if day_part in AFTERNOON_PARTS:
        return "afternoon"
    return "other"


def focus_session_rows(db: Session) -> list[dict]:
    zone = ZoneInfo(get_settings().timezone)
    rows = []
    query = (
        db.query(WorkSession, DailyTask, Task)
        .join(DailyTask, WorkSession.daily_task_id == DailyTask.id)
        .join(Task, DailyTask.task_id == Task.id)
    )
    for work, daily, task in query:
        local = work.started_at.astimezone(zone)
        day_part = classify_day_part(local)
        rows.append(
            {
                "work": work,
                "daily": daily,
                "task": task,
                "local": local,
                "local_date": local.date(),
                "day_part": day_part,
                "period": classify_focus_period(day_part),
                "weekday": local.weekday(),
                "positive": work.outcome in POSITIVE_OUTCOMES,
                "interrupted": work.interrupted,
            }
        )
    return rows


def attach_week_index(rows: list[dict], start: date) -> list[dict]:
    for row in rows:
        row["week"] = scenario_week_index(row["local_date"], start)
    return rows


def period_summary(rows: list[dict], period: str) -> dict:
    matched = [row for row in rows if row["period"] == period]
    n = len(matched)
    positive = sum(row["positive"] for row in matched)
    return {
        "n": n,
        "positive": positive,
        "rate": positive / n if n else 0.0,
    }


def morning_afternoon_summary(rows: list[dict]) -> dict:
    morning = period_summary(rows, "morning")
    afternoon = period_summary(rows, "afternoon")
    return {
        "morning_n": morning["n"],
        "morning_rate": morning["rate"],
        "afternoon_n": afternoon["n"],
        "afternoon_rate": afternoon["rate"],
        "gap": morning["rate"] - afternoon["rate"],
        "n": len(rows),
    }


def weekly_focus_rows(rows: list[dict], start: date) -> list[dict]:
    attach_week_index(rows, start)
    weeks = sorted({row["week"] for row in rows})
    series = []
    for week in weeks:
        matched = [row for row in rows if row["week"] == week]
        summary = morning_afternoon_summary(matched)
        summary["week"] = week
        summary["week_start"] = week_start_date(start, week)
        series.append(summary)
    return series


def rows_for_weeks(
    rows: list[dict],
    start: date,
    first: int,
    last: int,
) -> list[dict]:
    attach_week_index(rows, start)
    return [
        row for row in rows if first <= row["week"] <= last
    ]


def rows_for_dates(
    rows: list[dict],
    first: date,
    last: date,
) -> list[dict]:
    return [
        row
        for row in rows
        if first <= row["local_date"] <= last
    ]


def rolling_gap(weekly: list[dict], window: int) -> list[dict]:
    rolled = []
    for index in range(len(weekly)):
        chunk = weekly[max(0, index + 1 - window): index + 1]
        if len(chunk) < window:
            continue
        morning_n = sum(row["morning_n"] for row in chunk)
        afternoon_n = sum(row["afternoon_n"] for row in chunk)
        morning_pos = sum(
            row["morning_rate"] * row["morning_n"] for row in chunk
        )
        afternoon_pos = sum(
            row["afternoon_rate"] * row["afternoon_n"] for row in chunk
        )
        morning_rate = morning_pos / morning_n if morning_n else 0.0
        afternoon_rate = (
            afternoon_pos / afternoon_n if afternoon_n else 0.0
        )
        rolled.append(
            {
                "through_week": chunk[-1]["week"],
                "window": window,
                "morning_n": morning_n,
                "morning_rate": morning_rate,
                "afternoon_n": afternoon_n,
                "afternoon_rate": afternoon_rate,
                "gap": morning_rate - afternoon_rate,
            }
        )
    return rolled


def infer_start_monday(db: Session) -> date | None:
    first = db.query(DailyTask.date).order_by(DailyTask.date).first()
    if first is None:
        return None
    start = first[0]
    return start - timedelta(days=start.weekday())


def max_week_index(rows: list[dict], start: date) -> int:
    attach_week_index(rows, start)
    return max(row["week"] for row in rows)


def last_n_weeks(rows: list[dict], start: date, weeks: int) -> list[dict]:
    last = max_week_index(rows, start)
    return rows_for_weeks(rows, start, last - weeks + 1, last)


def preceding_n_weeks(
    rows: list[dict],
    start: date,
    *,
    recent_weeks: int,
    preceding_weeks: int,
) -> list[dict]:
    last = max_week_index(rows, start)
    recent_first = last - recent_weeks + 1
    return rows_for_weeks(
        rows,
        start,
        recent_first - preceding_weeks,
        recent_first - 1,
    )
