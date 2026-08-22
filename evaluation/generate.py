"""CLI for isolated agent-evaluation dataset generation.

Usage:

    python -m evaluation.generate temporal_patterns
    python -m evaluation.generate interruptions_dependencies
    python -m evaluation.generate task_age_abandonment --print-rates
    python -m evaluation.generate planning_workload --print-rates
    python -m evaluation.generate behaviour_change --print-rates
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
from evaluation.observe import print_observed_rates
from evaluation.result import GenerationResult
from evaluation.scenarios.interruptions_dependencies import (
    DEFAULT_SEED as INTERRUPTIONS_DEPENDENCIES_SEED,
    generate_interruptions_dependencies,
)
from evaluation.scenarios.planning_workload import (
    DEFAULT_SEED as PLANNING_WORKLOAD_SEED,
    generate_planning_workload,
)
from evaluation.scenarios.behaviour_change import (
    DEFAULT_SEED as BEHAVIOUR_CHANGE_SEED,
    generate_behaviour_change,
)
from evaluation.scenarios.task_age_abandonment import (
    DEFAULT_SEED as TASK_AGE_ABANDONMENT_SEED,
    generate_task_age_abandonment,
)
from evaluation.scenarios.temporal_patterns import (
    DEFAULT_SEED as TEMPORAL_PATTERNS_SEED,
    generate_temporal_patterns,
)


GENERATORS = {
    "temporal_patterns": generate_temporal_patterns,
    "interruptions_dependencies": generate_interruptions_dependencies,
    "task_age_abandonment": generate_task_age_abandonment,
    "planning_workload": generate_planning_workload,
    "behaviour_change": generate_behaviour_change,
}
DEFAULT_SEEDS = {
    "temporal_patterns": TEMPORAL_PATTERNS_SEED,
    "interruptions_dependencies": INTERRUPTIONS_DEPENDENCIES_SEED,
    "task_age_abandonment": TASK_AGE_ABANDONMENT_SEED,
    "planning_workload": PLANNING_WORKLOAD_SEED,
    "behaviour_change": BEHAVIOUR_CHANGE_SEED,
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
            "Random seed. Defaults to the selected scenario's "
            "fixed seed."
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
            "Print observed rates and a bounded stuck-title "
            "sample after generation. Operator diagnostics only."
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
                DEFAULT_SEEDS[args.scenario]
                if args.seed is None
                else args.seed
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
                print_observed_rates(db)
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


if __name__ == "__main__":
    sys.exit(main())
