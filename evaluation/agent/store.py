"""Persist evaluation runs under a gitignored results directory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evaluation.agent.model import API_KEY_ENV


DEFAULT_RESULTS_DIR = (
    Path(__file__).resolve().parent / "results"
)
SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "EVAL_AGENT_API_KEY",
        "openai_api_key",
        "anthropic_api_key",
        "bearer",
    }
)


def persist_run(
    record: dict,
    *,
    results_dir: Path | None = None,
) -> Path:
    """Write one run record as JSON. Refuses to serialize secrets."""
    _assert_no_secrets(record)
    directory = results_dir or DEFAULT_RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = record.get("timestamp") or _utc_timestamp()
    case_id = record.get("case_id") or "unknown"
    safe_stamp = str(timestamp).replace(":", "").replace("+", "")
    path = directory / f"{safe_stamp}_{case_id}.json"
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def utc_timestamp() -> str:
    return _utc_timestamp()


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _assert_no_secrets(record: dict) -> None:
    keys = _collect_keys(record)
    forbidden = keys & SECRET_KEYS
    if forbidden:
        raise RuntimeError(
            "Refusing to persist secret-like keys: "
            + ", ".join(sorted(forbidden))
        )
    secret = os.environ.get(API_KEY_ENV, "").strip()
    if not secret:
        return
    blob = json.dumps(record)
    if secret in blob:
        raise RuntimeError(
            "Refusing to persist configured API key material."
        )


def _collect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_collect_keys(child))
    return keys
