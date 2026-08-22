"""CLI for isolated agent-evaluation dataset generation.

Usage:

    python -m evaluation.generate temporal_patterns
    python -m evaluation.generate temporal_patterns --export-csv
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from evaluation.database import (
    create_eval_engine,
    require_eval_database_url,
    reset_eval_schema,
)
from evaluation.export import export_evaluation_csv
from evaluation.scenarios.temporal_patterns import (
    DEFAULT_SEED,
    GenerationResult,
    generate_temporal_patterns,
)


GENERATORS = {
    "temporal_patterns": generate_temporal_patterns,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic evaluation dataset "
            "into system1_agent_eval."
        )
    )
    parser.add_argument(
        "scenario",
        choices=sorted(GENERATORS),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Random seed. Defaults to the scenario's "
            f"fixed seed ({DEFAULT_SEED} for temporal_patterns)."
        ),
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help=(
            "Write tasks.csv, daily_tasks.csv, and "
            "work_sessions.csv for manual inspection."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="evaluation/output",
        help="Directory for optional CSV export.",
    )
    parser.add_argument(
        "--print-rates",
        action="store_true",
        help=(
            "Print observed positive-outcome rates after "
            "generation. Operator diagnostics only."
        ),
    )
    args = parser.parse_args(argv)

    url = require_eval_database_url()
    engine = create_eval_engine(url)

    try:
        reset_eval_schema(engine)
        SessionLocal = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )
        with SessionLocal() as db:
            seed = (
                DEFAULT_SEED if args.seed is None else args.seed
            )
            result = GENERATORS[args.scenario](
                db,
                seed=seed,
                timezone_name=get_settings().timezone,
            )
            db.commit()

            if args.export_csv:
                export_evaluation_csv(db, args.output_dir)

            _print_summary(result)
            if args.print_rates:
                _print_observed_rates(db)
    finally:
        engine.dispose()

    return 0


def _print_summary(result: GenerationResult) -> None:
    print(f"scenario: {result.scenario}")
    print(f"seed: {result.seed}")
    print(
        "date range: "
        f"{result.start_date.isoformat()} to "
        f"{result.end_date.isoformat()}"
    )
    print(f"Task count: {result.task_count}")
    print(f"DailyTask count: {result.daily_task_count}")
    print(f"WorkSession count: {result.work_session_count}")


def _print_observed_rates(db) -> None:
    from collections import defaultdict
    from zoneinfo import ZoneInfo

    from app.models import DailyTask, Task, WorkSession
    from evaluation.catalog import parse_task_category
    from evaluation.clock import DAY_PARTS, classify_day_part

    positive = {"progress", "complete"}
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri")
    zone = ZoneInfo(get_settings().timezone)
    by_weekday = defaultdict(list)
    by_part = defaultdict(list)
    by_cell = defaultdict(list)
    interruption_by_part = defaultdict(list)
    category_by_part = defaultdict(list)

    rows = (
        db.query(WorkSession, DailyTask, Task)
        .join(DailyTask, WorkSession.daily_task_id == DailyTask.id)
        .join(Task, DailyTask.task_id == Task.id)
        .all()
    )
    for work, _daily, task in rows:
        local = work.started_at.astimezone(zone)
        weekday = weekdays[local.weekday()]
        part = classify_day_part(local)
        is_positive = work.outcome in positive
        by_weekday[weekday].append(is_positive)
        by_part[part].append(is_positive)
        by_cell[(weekday, part)].append(is_positive)
        interruption_by_part[part].append(work.interrupted)
        category_by_part[part].append(parse_task_category(task.title))

    def _fmt(values: list) -> str:
        if not values:
            return "n=0"
        rate = sum(values) / len(values)
        return f"{rate:6.1%}  n={len(values)}"

    print()
    print("positive outcome rate by weekday")
    for weekday in weekdays:
        print(f"  {weekday}: {_fmt(by_weekday[weekday])}")

    print("positive outcome rate by day-part")
    for part in DAY_PARTS:
        print(f"  {part}: {_fmt(by_part[part])}")

    print("positive outcome rate by weekday + day-part")
    for weekday in weekdays:
        for part in DAY_PARTS:
            print(
                f"  {weekday} {part}: "
                f"{_fmt(by_cell[(weekday, part)])}"
            )

    print("interruption rate by day-part")
    for part in DAY_PARTS:
        print(f"  {part}: {_fmt(interruption_by_part[part])}")

    print("category mix by day-part")
    for part in DAY_PARTS:
        labels = [item for item in category_by_part[part] if item]
        total = len(labels) or 1
        mix = ", ".join(
            f"{name}={labels.count(name) / total:.0%}"
            for name in sorted(set(labels))
        )
        print(f"  {part}: {mix}")


if __name__ == "__main__":
    sys.exit(main())
