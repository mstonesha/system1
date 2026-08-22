"""Planning and workload diagnostics for evaluation datasets.

These helpers derive planning metrics from existing Task / DailyTask /
WorkSession rows. They do not add production fields and must not load
ground-truth YAML.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import parse_task_category


POSITIVE_OUTCOMES = frozenset({"progress", "complete"})
HEAVY_PLANNED_THRESHOLD = 70
NORMAL_PLANNED_THRESHOLD = 64


def scenario_week_index(day: date, start: date) -> int:
    return (day - start).days // 7 + 1


def week_start_date(start: date, week_index: int) -> date:
    return start + timedelta(weeks=week_index - 1)


def unused_planned_breakdown(db: Session) -> dict:
    """Classify unused DailyTask planned sessions by why they were unused."""
    totals = {
        "planned_sessions": 0,
        "actual_sessions": 0,
        "unused_planned_sessions": 0,
        "unused_due_to_completion": 0,
        "unused_while_task_remained_active": 0,
        "unused_with_terminal_abandonment": 0,
        "daily_tasks_with_unused": 0,
        "case_a_active_shortfall": 0,
        "case_b_early_completion": 0,
        "case_c_extra_work": 0,
        "case_d_executed_as_planned": 0,
    }
    dailies = db.query(DailyTask).order_by(DailyTask.id).all()
    for daily in dailies:
        planned = daily.planned_sessions or 0
        actual = len(daily.work_sessions)
        unused = max(0, planned - actual)
        totals["planned_sessions"] += planned
        totals["actual_sessions"] += actual
        totals["unused_planned_sessions"] += unused
        if unused:
            totals["daily_tasks_with_unused"] += 1
        if daily.state == "completed" and unused > 0:
            totals["unused_due_to_completion"] += unused
            totals["case_b_early_completion"] += 1
        elif daily.state == "abandoned" and unused > 0:
            totals["unused_with_terminal_abandonment"] += unused
        elif unused > 0:
            totals["unused_while_task_remained_active"] += unused
            totals["case_a_active_shortfall"] += 1
        if actual > planned > 0:
            totals["case_c_extra_work"] += 1
        elif planned > 0 and actual == planned:
            totals["case_d_executed_as_planned"] += 1
    planned = totals["planned_sessions"]
    actual = totals["actual_sessions"]
    totals["execution_ratio"] = (
        actual / planned if planned else 0.0
    )
    totals["planned_over_actual_gap"] = (
        (planned - actual) / actual if actual else 0.0
    )
    return totals


def weekly_workload_rows(db: Session, start: date) -> list[dict]:
    buckets: dict[int, dict] = {}
    dailies = db.query(DailyTask).all()
    for daily in dailies:
        week = scenario_week_index(daily.date, start)
        row = buckets.setdefault(
            week,
            {
                "week": week,
                "week_start": week_start_date(start, week),
                "daily_task_count": 0,
                "planned_sessions": 0,
                "actual_sessions": 0,
                "positive": 0,
                "session_count": 0,
            },
        )
        row["daily_task_count"] += 1
        row["planned_sessions"] += daily.planned_sessions or 0
        for session in daily.work_sessions:
            row["actual_sessions"] += 1
            row["session_count"] += 1
            if session.outcome in POSITIVE_OUTCOMES:
                row["positive"] += 1

    rows = []
    for week in sorted(buckets):
        row = buckets[week]
        n = row["session_count"]
        planned = row["planned_sessions"]
        row["positive_rate"] = row["positive"] / n if n else 0.0
        if planned >= HEAVY_PLANNED_THRESHOLD:
            row["observed_regime"] = "heavy"
        elif planned <= NORMAL_PLANNED_THRESHOLD:
            row["observed_regime"] = "normal"
        else:
            row["observed_regime"] = "transitional"
        rows.append(row)
    return rows


def completed_task_effort_records(db: Session) -> list[dict]:
    records = []
    tasks = (
        db.query(Task)
        .filter(Task.status == "completed")
        .order_by(Task.id)
        .all()
    )
    for task in tasks:
        if not task.estimated_sessions:
            continue
        sessions = [
            session
            for daily in task.daily_tasks
            for session in daily.work_sessions
        ]
        if not sessions:
            continue
        actual = len(sessions)
        estimated = task.estimated_sessions
        if actual < estimated:
            band = "below"
        elif actual == estimated:
            band = "near"
        else:
            band = "above"
        records.append(
            {
                "task": task,
                "category": parse_task_category(task.title),
                "estimated_sessions": estimated,
                "actual_sessions": actual,
                "ratio": actual / estimated,
                "band": band,
            }
        )
    return records


def effort_summary(records: list[dict]) -> dict:
    if not records:
        return {
            "n": 0,
            "mean_estimated": 0.0,
            "mean_actual": 0.0,
            "mean_ratio": 0.0,
            "median_ratio": 0.0,
            "below": 0,
            "near": 0,
            "above": 0,
        }
    ratios = sorted(row["ratio"] for row in records)
    mid = len(ratios) // 2
    if len(ratios) % 2:
        median = ratios[mid]
    else:
        median = (ratios[mid - 1] + ratios[mid]) / 2
    return {
        "n": len(records),
        "mean_estimated": (
            sum(row["estimated_sessions"] for row in records)
            / len(records)
        ),
        "mean_actual": (
            sum(row["actual_sessions"] for row in records)
            / len(records)
        ),
        "mean_ratio": sum(ratios) / len(ratios),
        "median_ratio": median,
        "below": sum(row["band"] == "below" for row in records),
        "near": sum(row["band"] == "near" for row in records),
        "above": sum(row["band"] == "above" for row in records),
    }


def regime_positive_rates(weeks: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in weeks:
        grouped[row["observed_regime"]].append(row)
    summary = {}
    for name, rows in grouped.items():
        sessions = sum(row["session_count"] for row in rows)
        positive = sum(row["positive"] for row in rows)
        summary[name] = {
            "weeks": len(rows),
            "session_count": sessions,
            "positive_rate": (
                positive / sessions if sessions else 0.0
            ),
            "mean_planned": (
                sum(row["planned_sessions"] for row in rows)
                / len(rows)
            ),
            "mean_daily_tasks": (
                sum(row["daily_task_count"] for row in rows)
                / len(rows)
            ),
            "mean_actual": (
                sum(row["actual_sessions"] for row in rows)
                / len(rows)
            ),
        }
    return summary
