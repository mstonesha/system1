"""Optional CSV export for manual inspection of evaluation data."""

import csv
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DailyTask, Task, WorkSession


def export_evaluation_csv(db: Session, output_dir: str | Path) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    _write_csv(
        destination / "tasks.csv",
        db.query(Task).order_by(Task.id).all(),
        (
            "id",
            "title",
            "description",
            "status",
            "priority",
            "estimated_sessions",
            "due_date",
            "completed_at",
            "sort_order",
            "parent_task_id",
            "created_at",
        ),
    )
    _write_csv(
        destination / "daily_tasks.csv",
        db.query(DailyTask).order_by(DailyTask.id).all(),
        (
            "id",
            "task_id",
            "date",
            "planned_sessions",
            "state",
            "sort_order",
            "created_at",
        ),
    )
    _write_csv(
        destination / "work_sessions.csv",
        db.query(WorkSession).order_by(WorkSession.id).all(),
        (
            "id",
            "daily_task_id",
            "started_at",
            "ended_at",
            "planned_duration_seconds",
            "actual_duration_seconds",
            "session_state",
            "outcome",
            "interrupted",
            "note",
            "created_at",
        ),
    )

    return destination


def _write_csv(path: Path, rows: list, field_names: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    name: _csv_value(getattr(row, name))
                    for name in field_names
                }
            )


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
