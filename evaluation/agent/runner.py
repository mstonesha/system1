"""CLI for Step 1 agent evaluation (Datasets A and F).

Usage:

    python -m evaluation.agent.runner --scenario temporal_patterns --dry-run
    python -m evaluation.agent.runner --scenario noise_control --dry-run
    python -m evaluation.agent.runner --scenario temporal_patterns --prompt-version temporal-v2 --dry-run
    python -m evaluation.agent.runner --scenario noise_control --prompt-version temporal-v2 --dry-run
    python -m evaluation.agent.runner --scenario temporal_patterns --prompt-version temporal-v3 --dry-run
    python -m evaluation.agent.runner --scenario noise_control --prompt-version temporal-v3 --dry-run
    python -m evaluation.agent.runner --scenario temporal_patterns
    python -m evaluation.agent.runner --scenario noise_control

Live runs require EVAL_AGENT_API_KEY, EVAL_AGENT_BASE_URL, and
EVAL_AGENT_MODEL. Dry-run generates the scenario, builds evidence,
and renders the prompt without calling a model.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from evaluation.agent.evidence import (
    ALLOWED_CASE_IDS,
    build_temporal_evidence,
    evidence_ids,
)
from evaluation.agent.model import (
    AnalysisModel,
    load_configured_model,
    missing_provider_message,
)
from evaluation.agent.prompt import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_VERSIONS,
    render_prompt,
)
from evaluation.agent.store import persist_run, utc_timestamp
from evaluation.agent.types import parse_agent_analysis
from evaluation.database import (
    create_eval_engine,
    require_eval_database_url,
    reset_eval_schema,
)
from evaluation.generate import DEFAULT_SEEDS, GENERATORS


SCENARIO_CASE_IDS = {
    "temporal_patterns": "case_a",
    "noise_control": "case_f",
}
GROUND_TRUTH_DIR = (
    Path(__file__).resolve().parents[1] / "ground_truth"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the evaluation-only temporal analysis harness "
            "against Dataset A (temporal_patterns) or Dataset F "
            "(noise_control)."
        )
    )
    parser.add_argument(
        "--scenario",
        required=True,
        choices=sorted(SCENARIO_CASE_IDS),
        help=(
            "Synthetic scenario to generate. The model receives "
            "an opaque case_id, not this name."
        ),
    )
    parser.add_argument(
        "--prompt-version",
        choices=PROMPT_VERSIONS,
        default=DEFAULT_PROMPT_VERSION,
        help=(
            "Evidence and prompt contract. Default "
            f"{DEFAULT_PROMPT_VERSION} preserves the original "
            "baseline. temporal-v2 adds weekly "
            "morning/afternoon series. temporal-v3 keeps "
            "that evidence and revises the pattern-reasoning "
            "instructions."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Generate evidence and render the model prompt "
            "without calling a provider."
        ),
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the exact system and user prompt text.",
    )
    parser.add_argument(
        "--show-ground-truth",
        action="store_true",
        help=(
            "Print hidden ground truth after the review output. "
            "Never included in the model prompt."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override the scenario's fixed seed.",
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Directory for persisted run files.",
    )
    args = parser.parse_args(argv)

    case_id = SCENARIO_CASE_IDS[args.scenario]
    if case_id not in ALLOWED_CASE_IDS:
        print(f"Unsupported case_id: {case_id}", file=sys.stderr)
        return 2

    model: AnalysisModel | None = None
    if not args.dry_run:
        model = load_configured_model()
        if model is None:
            print(missing_provider_message(), file=sys.stderr)
            return 2

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
            record = run_case(
                db,
                case_id=case_id,
                from_date=result.start_date,
                to_date=result.end_date,
                dry_run=args.dry_run,
                model=model,
                prompt_version=args.prompt_version,
            )
    finally:
        engine.dispose()

    results_dir = (
        Path(args.results_dir) if args.results_dir else None
    )
    path = persist_run(record, results_dir=results_dir)
    _print_review(
        record,
        results_path=path,
        print_prompt=args.print_prompt,
        ground_truth_text=(
            _load_ground_truth(args.scenario)
            if args.show_ground_truth
            else None
        ),
    )
    if record["parse_status"] in {"invalid_json", "invalid_schema"}:
        return 1
    return 0


def run_case(
    db: Session,
    *,
    case_id: str,
    from_date: date,
    to_date: date,
    dry_run: bool,
    model: AnalysisModel | None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> dict:
    """Build evidence, prompt, and optionally call the model.

    Does not load or attach hidden ground truth.
    """
    evidence = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id=case_id,
        version=prompt_version,
    )
    prompt = render_prompt(evidence, version=prompt_version)
    timestamp = utc_timestamp()
    if dry_run:
        return {
            "case_id": case_id,
            "timestamp": timestamp,
            "model": "dry-run",
            "prompt_version": prompt.version,
            "dry_run": True,
            "evidence": evidence,
            "prompt": prompt.to_dict(),
            "raw_response": None,
            "analysis": None,
            "parse_status": "dry_run",
            "validation_errors": [],
            "unknown_evidence_refs": [],
        }

    if model is None:
        raise RuntimeError("A model is required unless dry_run is set.")

    raw = model.analyse(prompt)
    parsed = parse_agent_analysis(
        raw.text,
        known_ids=evidence_ids(evidence),
    )
    return {
        "case_id": case_id,
        "timestamp": timestamp,
        "model": raw.model_identifier,
        "prompt_version": prompt.version,
        "dry_run": False,
        "evidence": evidence,
        "prompt": prompt.to_dict(),
        "raw_response": raw.text,
        "analysis": (
            parsed.analysis.to_dict()
            if parsed.analysis is not None
            else None
        ),
        "parse_status": parsed.status,
        "validation_errors": list(parsed.errors),
        "unknown_evidence_refs": list(parsed.unknown_evidence_refs),
    }


def _print_review(
    record: dict,
    *,
    results_path: Path,
    print_prompt: bool,
    ground_truth_text: str | None,
) -> None:
    evidence = record["evidence"]
    period = evidence["period"]
    print(f"case_id: {record['case_id']}")
    print(f"parse_status: {record['parse_status']}")
    print(f"model: {record['model']}")
    print(f"prompt_version: {record['prompt_version']}")
    print(
        "period: "
        f"{period['from']} to {period['to']} "
        f"({period['timezone']})"
    )
    print(f"total_sessions: {period['total_sessions']}")
    print(
        "groups: "
        f"weekday={len(evidence['weekday_outcomes'])} "
        f"daypart={len(evidence['daypart_outcomes'])} "
        f"weekday_daypart="
        f"{len(evidence['weekday_daypart_outcomes'])}"
        + (
            " weekly="
            f"{len(evidence['weekly_morning_afternoon'])}"
            if "weekly_morning_afternoon" in evidence
            else ""
        )
    )
    _print_group_table(
        "weekday_outcomes",
        evidence["weekday_outcomes"],
        label_key="weekday",
    )
    _print_group_table(
        "daypart_outcomes",
        evidence["daypart_outcomes"],
        label_key="daypart",
    )
    window = evidence["morning_afternoon"]
    print("morning_afternoon:")
    print(
        "  morning n="
        f"{window['morning']['n']} "
        f"positive_rate={window['morning']['positive_rate']}"
    )
    print(
        "  afternoon n="
        f"{window['afternoon']['n']} "
        f"positive_rate={window['afternoon']['positive_rate']}"
    )
    print(f"  positive_rate_gap={window['positive_rate_gap']}")
    if "weekly_morning_afternoon" in evidence:
        weeks = evidence["weekly_morning_afternoon"]
        print(f"weekly_morning_afternoon: {len(weeks)}")
        for row in weeks:
            print(
                f"  {row['week_start']} "
                f"morning_n={row['morning_n']} "
                f"afternoon_n={row['afternoon_n']} "
                f"gap={row['positive_rate_gap']}"
            )
    prompt_chars = len(record["prompt"]["system"]) + len(
        record["prompt"]["user"]
    )
    print(f"prompt_characters: {prompt_chars}")
    print(f"results: {results_path}")

    if print_prompt:
        print("--- model prompt (system) ---")
        print(record["prompt"]["system"])
        print("--- model prompt (user) ---")
        print(record["prompt"]["user"])

    analysis = record.get("analysis")
    if analysis is not None:
        _print_statements("observations", analysis["observations"])
        _print_statements("patterns", analysis["patterns"])
        _print_statements("hypotheses", analysis["hypotheses"])
        _print_statements(
            "insufficient_evidence",
            analysis["insufficient_evidence"],
        )
        _print_statements(
            "suggested_drilldowns",
            analysis["suggested_drilldowns"],
        )
    elif record["parse_status"] not in {"dry_run"}:
        print("structured analysis: unavailable")
        if record.get("validation_errors"):
            print("validation_errors:")
            for error in record["validation_errors"]:
                print(f"  - {error}")
        raw = record.get("raw_response")
        if raw:
            print("--- raw response ---")
            print(raw)

    if ground_truth_text:
        print(
            "--- evaluator only: hidden ground truth "
            "(not sent to the model) ---"
        )
        print(ground_truth_text.rstrip())


def _print_group_table(
    title: str,
    rows: list[dict],
    *,
    label_key: str,
) -> None:
    print(f"{title}:")
    for row in rows:
        print(
            f"  {row[label_key]} n={row['n']} "
            f"positive_rate={row['positive_rate']}"
        )


def _print_statements(title: str, items: list[dict]) -> None:
    print(f"{title}:")
    if not items:
        print("  (none)")
        return
    for item in items:
        refs = item.get("evidence_refs") or []
        suffix = f" [{', '.join(refs)}]" if refs else ""
        print(f"  - {item['statement']}{suffix}")


def _load_ground_truth(scenario: str) -> str:
    path = GROUND_TRUTH_DIR / f"{scenario}.yaml"
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
