import ast
import inspect
import json
import urllib.error
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.models import WorkSession
from app.time import UTC
from evaluation.agent.evidence import (
    build_temporal_evidence,
    evidence_ids,
    serialize_evidence,
)
from evaluation.agent.model import (
    API_KEY_ENV,
    BASE_URL_ENV,
    ERROR_BODY_LIMIT,
    MODEL_ENV,
    OpenAICompatibleModel,
    StubAnalysisModel,
    load_configured_model,
)
from evaluation.agent.prompt import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_VERSION,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    PROMPT_VERSION_V3,
    SYSTEM_INSTRUCTIONS_V1,
    SYSTEM_INSTRUCTIONS_V2,
    SYSTEM_INSTRUCTIONS_V3,
    ModelPrompt,
    render_prompt,
)
from evaluation.agent.runner import run_case
from evaluation.agent.store import persist_run
from evaluation.agent.types import parse_agent_analysis


LONDON = ZoneInfo("Europe/London")
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
AGENT_DIR = REPO_ROOT / "evaluation" / "agent"
FORBIDDEN_IN_MODEL_INPUT = (
    "temporal_patterns",
    "noise_control",
    "dataset a",
    "dataset f",
    "ground_truth",
    "expected_patterns",
    "expected_non_patterns",
    "agent_should",
    "monday_morning_is_a_strong_negative_exception",
    "no_robust_behavioural_pattern",
)
VALID_ANALYSIS = {
    "observations": [
        {
            "statement": "Friday has the highest weekday rate.",
            "evidence_refs": ["weekday:friday"],
        }
    ],
    "patterns": [
        {
            "statement": "No robust weekday pattern is supported.",
            "evidence_refs": [
                "weekday:monday",
                "weekday:friday",
            ],
        }
    ],
    "hypotheses": [],
    "insufficient_evidence": [
        {
            "statement": "Evening n is too small to rank as best.",
            "evidence_refs": ["daypart:evening"],
        }
    ],
    "suggested_drilldowns": [],
}


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
        session_state="completed",
        outcome=outcome,
    )
    db.add(work_session)
    db.commit()
    db.refresh(work_session)
    return work_session


def _seed_temporal_sessions(db, make_task, make_daily_task):
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 18, 30),
        outcome="complete",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 12, 7, 0),
        outcome="progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 10, 0),
        outcome="progress",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 13, 13, 0),
        outcome="stuck",
    )
    _add_completed(
        db,
        make_task,
        make_daily_task,
        _local(2026, 1, 16, 10, 0),
        outcome="complete",
    )


def _assert_no_model_input_leak(text: str) -> None:
    lowered = text.lower()
    for token in FORBIDDEN_IN_MODEL_INPUT:
        assert token.lower() not in lowered, token


def test_application_code_does_not_import_evaluation():
    for path in APP_DIR.rglob("*.py"):
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


def test_evidence_module_uses_production_analytics():
    text = (AGENT_DIR / "evidence.py").read_text(encoding="utf-8")
    source = inspect.getsource(build_temporal_evidence)
    assert "from app.analytics.outcomes import" in text
    assert "from app.analytics.change import" in text
    assert "session_outcomes_by_weekday" in source
    assert "session_outcomes_by_daypart" in source
    assert "session_outcomes_by_weekday_daypart" in source
    assert "morning_afternoon_window" in source
    assert "weekly_morning_afternoon_outcomes" in text
    assert "evaluation.observe" not in text
    assert "yaml.safe_load" not in text
    assert "ground_truth" not in text
    assert "temporal_patterns" not in text
    assert "noise_control" not in text


def test_prompt_and_run_case_do_not_load_ground_truth():
    prompt_text = (AGENT_DIR / "prompt.py").read_text(
        encoding="utf-8"
    )
    run_source = inspect.getsource(run_case)
    assert "yaml.safe_load" not in prompt_text
    assert "ground_truth" not in prompt_text
    assert "temporal_patterns" not in prompt_text
    assert "noise_control" not in prompt_text
    assert "ground_truth" not in run_source
    assert "yaml" not in run_source


def test_evidence_builder_is_deterministic_and_keeps_small_n(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    kwargs = dict(
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_a",
    )
    first = build_temporal_evidence(db, **kwargs)
    second = build_temporal_evidence(db, **kwargs)
    assert serialize_evidence(first) == serialize_evidence(second)
    assert first["case_id"] == "case_a"
    assert first["period"]["total_sessions"] == 5
    assert first["period"]["timezone"] == "Europe/London"
    monday = next(
        row
        for row in first["weekday_outcomes"]
        if row["weekday"] == "Monday"
    )
    assert monday["n"] == 2
    assert monday["id"] == "weekday:monday"
    assert monday["complete"] == 1
    assert monday["progress"] == 1
    evening = next(
        row
        for row in first["daypart_outcomes"]
        if row["daypart"] == "evening"
    )
    assert evening["n"] == 1
    assert evening["positive_rate"] == 1.0
    monday_evening = next(
        row
        for row in first["weekday_daypart_outcomes"]
        if row["id"] == "weekday_daypart:monday:evening"
    )
    assert monday_evening["n"] == 1
    window = first["morning_afternoon"]
    assert window["morning"]["n"] == 2
    assert window["afternoon"]["n"] == 1
    blob = serialize_evidence(first)
    _assert_no_model_input_leak(blob)
    assert "interruption" not in first
    assert "stuck_task" not in blob
    assert "planned_sessions" not in blob
    assert set(first) == {
        "case_id",
        "period",
        "weekday_outcomes",
        "daypart_outcomes",
        "weekday_daypart_outcomes",
        "morning_afternoon",
    }
    assert "weekly_morning_afternoon" not in first


def test_evidence_builder_rejects_semantic_case_id(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    with pytest.raises(ValueError, match="opaque"):
        build_temporal_evidence(
            db,
            from_date=date(2026, 1, 12),
            to_date=date(2026, 1, 16),
            case_id="temporal_patterns",
        )


def test_evidence_builder_calls_production_analytics(
    db,
    make_task,
    make_daily_task,
    monkeypatch,
):
    from evaluation.agent import evidence as evidence_mod

    _seed_temporal_sessions(db, make_task, make_daily_task)
    calls: list[str] = []

    def wrap(name, fn):
        def inner(*args, **kwargs):
            calls.append(name)
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_weekday",
        wrap(
            "weekday",
            evidence_mod.session_outcomes_by_weekday,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_daypart",
        wrap(
            "daypart",
            evidence_mod.session_outcomes_by_daypart,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "session_outcomes_by_weekday_daypart",
        wrap(
            "cells",
            evidence_mod.session_outcomes_by_weekday_daypart,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "morning_afternoon_window",
        wrap(
            "window",
            evidence_mod.morning_afternoon_window,
        ),
    )
    monkeypatch.setattr(
        evidence_mod,
        "weekly_morning_afternoon_outcomes",
        wrap(
            "weeks",
            evidence_mod.weekly_morning_afternoon_outcomes,
        ),
    )
    build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
    )
    assert calls == ["weekday", "daypart", "cells", "window"]
    calls.clear()
    v2 = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
        version="temporal-v2",
    )
    assert calls == [
        "weekday",
        "daypart",
        "cells",
        "window",
        "weeks",
    ]
    assert "weekly_morning_afternoon" in v2
    calls.clear()
    v3 = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_f",
        version="temporal-v3",
    )
    assert calls == [
        "weekday",
        "daypart",
        "cells",
        "window",
        "weeks",
    ]
    assert serialize_evidence(v2) == serialize_evidence(v3)


def test_temporal_v1_prompt_is_preserved():
    assert PROMPT_VERSION == "temporal-v1"
    assert DEFAULT_PROMPT_VERSION == "temporal-v1"
    assert SYSTEM_INSTRUCTIONS_V1 is not SYSTEM_INSTRUCTIONS_V2
    assert "This analysis uses contract temporal-v2" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "Cross-sectional consistency" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "It is explicitly acceptable for patterns to be empty" not in (
        SYSTEM_INSTRUCTIONS_V1
    )
    assert "no robust pattern is supported" in (
        SYSTEM_INSTRUCTIONS_V1.lower()
    )
    assert "sample size" in SYSTEM_INSTRUCTIONS_V1.lower()
    prompt = render_prompt({"case_id": "case_a"}, version="temporal-v1")
    assert prompt.version == PROMPT_VERSION_V1
    assert prompt.system == SYSTEM_INSTRUCTIONS_V1


def test_temporal_v2_prompt_is_preserved():
    prompt = render_prompt(
        {"case_id": "case_f"},
        version="temporal-v2",
    )
    assert prompt.version == PROMPT_VERSION_V2
    assert prompt.system == SYSTEM_INSTRUCTIONS_V2
    text = prompt.system.lower()
    assert "this analysis uses contract temporal-v2" in text
    assert "cross-sectional consistency" in text
    assert "temporal stability" in text
    assert "does not establish temporal stability" in text
    assert "effect size" in text
    assert "patterns to be empty" in text
    assert "no robust pattern is supported" in text
    assert "sample size" in text
    assert "best" in text
    assert "converging evidence" not in text
    assert "do not automatically invalidate" not in text
    assert "qualified pattern" not in text
    assert "temporal-v3" not in prompt.system
    assert "temporal_patterns" not in prompt.system
    assert "noise_control" not in prompt.system
    _assert_no_model_input_leak(prompt.combined_text())


def test_temporal_v3_prompt_uses_converging_evidence():
    prompt = render_prompt(
        {"case_id": "case_a"},
        version="temporal-v3",
    )
    assert prompt.version == PROMPT_VERSION_V3
    assert prompt.system == SYSTEM_INSTRUCTIONS_V3
    text = prompt.system.lower()
    assert "this analysis uses contract temporal-v3" in text
    assert "converging" in text
    assert "do not automatically invalidate" in text
    assert "qualified pattern" in text
    assert "materially differs from comparable peers" in text
    assert "hard temporal-stability rule" in text
    assert "need not be universal" in text
    assert "patterns to be empty" in text
    assert "no robust pattern is supported" in text
    assert "sample size" in text
    assert "best" in text
    assert "do not extrapolate beyond the observed period" in text
    assert "temporal_patterns" not in prompt.system
    assert "noise_control" not in prompt.system
    assert "monday morning" not in text
    _assert_no_model_input_leak(prompt.combined_text())


def test_temporal_v2_weekly_evidence_uses_production_rows(
    db,
    make_task,
    make_daily_task,
):
    from app.analytics.change import weekly_morning_afternoon_outcomes

    _seed_temporal_sessions(db, make_task, make_daily_task)
    from_date = date(2026, 1, 12)
    to_date = date(2026, 1, 16)
    production = weekly_morning_afternoon_outcomes(
        db,
        from_date=from_date,
        to_date=to_date,
    )
    v1 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v1",
    )
    v2 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v2",
    )
    assert "weekly_morning_afternoon" not in v1
    weeks = v2["weekly_morning_afternoon"]
    assert len(weeks) == len(production.groups)
    assert production.groups
    for row, group in zip(weeks, production.groups):
        week_start = group.week_start_date.isoformat()
        assert row["id"] == f"week:{week_start}"
        assert row["week_start"] == week_start
        assert row["morning_n"] == group.morning_session_count
        assert row["morning_positive_rate"] == (
            group.morning_positive_rate
        )
        assert row["afternoon_n"] == group.afternoon_session_count
        assert row["afternoon_positive_rate"] == (
            group.afternoon_positive_rate
        )
        assert row["positive_rate_gap"] == group.positive_rate_gap
        assert "stable" not in row
        assert "significance" not in row
    known = evidence_ids(v2)
    assert weeks[0]["id"] in known
    parsed = parse_agent_analysis(
        json.dumps(
            {
                "observations": [
                    {
                        "statement": "A weekly gap is present.",
                        "evidence_refs": [weeks[0]["id"]],
                    }
                ],
                "patterns": [],
                "hypotheses": [],
                "insufficient_evidence": [
                    {
                        "statement": "An unknown week was cited.",
                        "evidence_refs": ["week:1999-01-04"],
                    }
                ],
                "suggested_drilldowns": [],
            }
        ),
        known_ids=known,
    )
    assert parsed.ok
    assert parsed.unknown_evidence_refs == ("week:1999-01-04",)
    prompt = render_prompt(v2, version="temporal-v2")
    assert weeks[0]["id"] in prompt.user
    assert "weekly_morning_afternoon" in prompt.user
    _assert_no_model_input_leak(prompt.combined_text())
    v3 = build_temporal_evidence(
        db,
        from_date=from_date,
        to_date=to_date,
        case_id="case_a",
        version="temporal-v3",
    )
    assert serialize_evidence(v2) == serialize_evidence(v3)
    v3_prompt = render_prompt(v3, version="temporal-v3")
    assert v3_prompt.version == "temporal-v3"
    assert serialize_evidence(v3) in v3_prompt.user
    assert v3_prompt.system != prompt.system



def test_prompt_includes_evidence_and_caution_rules(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    evidence = build_temporal_evidence(
        db,
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        case_id="case_a",
    )
    prompt = render_prompt(evidence)
    combined = prompt.combined_text()
    assert prompt.version == PROMPT_VERSION
    assert prompt.version == "temporal-v1"
    assert serialize_evidence(evidence) in prompt.user
    assert "weekly_morning_afternoon" not in prompt.user
    assert "observations" in prompt.system
    assert "patterns" in prompt.system
    assert "hypotheses" in prompt.system
    assert "insufficient_evidence" in prompt.system
    assert "sample size" in prompt.system.lower()
    assert "no robust pattern" in prompt.system.lower()
    assert "best" in prompt.system.lower()
    _assert_no_model_input_leak(combined)


def test_valid_response_parses_and_unknown_refs_are_listed():
    parsed = parse_agent_analysis(
        json.dumps(VALID_ANALYSIS),
        known_ids={"weekday:friday", "daypart:evening"},
    )
    assert parsed.ok
    assert parsed.analysis is not None
    assert parsed.analysis.observations[0].statement.startswith(
        "Friday"
    )
    assert parsed.unknown_evidence_refs == ("weekday:monday",)


def test_missing_required_field_fails_validation():
    payload = dict(VALID_ANALYSIS)
    del payload["patterns"]
    parsed = parse_agent_analysis(json.dumps(payload))
    assert parsed.status == "invalid_schema"
    assert parsed.analysis is None
    assert any("patterns" in error for error in parsed.errors)


def test_malformed_json_is_not_silently_repaired():
    raw = (
        "```json\n"
        + json.dumps(VALID_ANALYSIS)
        + "\n```"
    )
    parsed = parse_agent_analysis(raw)
    assert parsed.status == "invalid_json"
    assert parsed.analysis is None
    assert parsed.raw_text == raw


def test_trailing_comma_is_not_silently_repaired():
    raw = '{"observations": [], "patterns": [],}'
    parsed = parse_agent_analysis(raw)
    assert parsed.status == "invalid_json"
    assert parsed.analysis is None


def test_persist_writes_gitignored_dir_without_secrets(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(API_KEY_ENV, "sk-test-secret-value")
    record = {
        "case_id": "case_a",
        "timestamp": "2026-01-01T00:00:00Z",
        "model": "stub",
        "prompt_version": PROMPT_VERSION,
        "evidence": {"case_id": "case_a"},
        "parse_status": "ok",
    }
    path = persist_run(record, results_dir=tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["case_id"] == "case_a"
    blob = path.read_text(encoding="utf-8")
    assert "sk-test-secret-value" not in blob
    gitignore = (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "evaluation/agent/results/" in gitignore
    with pytest.raises(RuntimeError, match="secret"):
        persist_run(
            {
                "case_id": "case_a",
                "api_key": "sk-test-secret-value",
            },
            results_dir=tmp_path,
        )


def test_load_configured_model_requires_all_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert load_configured_model() is None
    monkeypatch.setenv(API_KEY_ENV, "  ")
    monkeypatch.setenv(BASE_URL_ENV, "https://example.invalid/v1")
    monkeypatch.setenv(MODEL_ENV, "example-model")
    assert load_configured_model() is None
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    model = load_configured_model()
    assert model is not None
    assert model.identifier == "example-model"


def test_run_case_dry_run_and_stub_do_not_need_live_api(
    db,
    make_task,
    make_daily_task,
):
    _seed_temporal_sessions(db, make_task, make_daily_task)
    dry = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
    )
    assert dry["parse_status"] == "dry_run"
    assert dry["prompt_version"] == "temporal-v1"
    assert "weekly_morning_afternoon" not in dry["evidence"]
    assert dry["raw_response"] is None
    _assert_no_model_input_leak(json.dumps(dry["evidence"]))
    _assert_no_model_input_leak(dry["prompt"]["system"])
    _assert_no_model_input_leak(dry["prompt"]["user"])

    stub = StubAnalysisModel(
        json.dumps(
            {
                "observations": [
                    {
                        "statement": "A cited missing slice.",
                        "evidence_refs": ["weekday:sunday"],
                    }
                ],
                "patterns": [],
                "hypotheses": [],
                "insufficient_evidence": [],
                "suggested_drilldowns": [],
            }
        )
    )
    live = run_case(
        db,
        case_id="case_f",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=False,
        model=stub,
    )
    assert live["parse_status"] == "ok"
    assert live["model"] == "stub"
    assert live["analysis"]["observations"]
    known = evidence_ids(live["evidence"])
    parsed = parse_agent_analysis(
        live["raw_response"],
        known_ids=known,
    )
    assert "weekday:sunday" not in known
    assert parsed.unknown_evidence_refs == ("weekday:sunday",)

    v2 = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
        prompt_version="temporal-v2",
    )
    assert v2["prompt_version"] == "temporal-v2"
    assert v2["prompt"]["version"] == "temporal-v2"
    assert "weekly_morning_afternoon" in v2["evidence"]
    week_id = v2["evidence"]["weekly_morning_afternoon"][0]["id"]
    assert week_id in evidence_ids(v2["evidence"])
    assert "temporal-v2" in v2["prompt"]["system"]
    _assert_no_model_input_leak(v2["prompt"]["system"])
    _assert_no_model_input_leak(v2["prompt"]["user"])

    v3 = run_case(
        db,
        case_id="case_a",
        from_date=date(2026, 1, 12),
        to_date=date(2026, 1, 16),
        dry_run=True,
        model=None,
        prompt_version="temporal-v3",
    )
    assert v3["prompt_version"] == "temporal-v3"
    assert v3["prompt"]["version"] == "temporal-v3"
    assert v3["evidence"] == v2["evidence"]
    assert "temporal-v3" in v3["prompt"]["system"]
    assert v3["prompt"]["system"] != v2["prompt"]["system"]
    _assert_no_model_input_leak(v3["prompt"]["system"])
    _assert_no_model_input_leak(v3["prompt"]["user"])


def test_cli_refuses_development_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://system1:system1@db:5432/system1",
    )
    from evaluation.agent.runner import main as agent_main

    with pytest.raises(
        RuntimeError,
        match="Refusing to generate evaluation data",
    ):
        agent_main(
            ["--scenario", "temporal_patterns", "--dry-run"]
        )


SECRET_KEY = "sk-test-secret-live-key"
PROMPT_MARKER = "unique-prompt-body-should-not-appear"


def _provider_prompt() -> ModelPrompt:
    return ModelPrompt(
        version=PROMPT_VERSION,
        system="system-instructions",
        user=PROMPT_MARKER,
    )


def _configured_model() -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        api_key=SECRET_KEY,
        base_url="https://example.invalid/v1",
        model="example-model",
    )


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        status,
        "Error",
        {"Content-Type": "application/json"},
        BytesIO(body),
    )


def _stub_http_error(monkeypatch, status: int, body: bytes) -> None:
    def fake_urlopen(request, timeout=None):
        raise _http_error(status, body)

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fake_urlopen,
    )


class _FakeCompletionsResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {"message": {"content": "{}"}}
                ]
            }
        ).encode("utf-8")


def test_openai_compatible_model_omits_temperature(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeCompletionsResponse()

    monkeypatch.setattr(
        "evaluation.agent.model.urllib.request.urlopen",
        fake_urlopen,
    )
    result = _configured_model().analyse(_provider_prompt())
    assert result.model_identifier == "example-model"
    assert captured["url"].endswith("/chat/completions")
    assert "temperature" not in captured["body"]
    assert captured["body"]["model"] == "example-model"
    assert captured["body"]["response_format"] == {
        "type": "json_object"
    }
    assert captured["body"]["messages"][0]["content"] == (
        "system-instructions"
    )
    assert captured["body"]["messages"][1]["content"] == (
        PROMPT_MARKER
    )


def test_openai_compatible_model_surfaces_http_400_fields(
    monkeypatch,
):
    payload = {
        "error": {
            "message": (
                "Unsupported parameter: 'response_format'."
            ),
            "type": "invalid_request_error",
            "param": "response_format",
            "code": "unsupported_parameter",
        }
    }
    _stub_http_error(
        monkeypatch,
        400,
        json.dumps(payload).encode("utf-8"),
    )
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message == (
        "Model HTTP 400: unsupported_parameter — "
        '"Unsupported parameter: \'response_format\'." '
        "(type: invalid_request_error; param: response_format)"
    )
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message


def test_openai_compatible_model_surfaces_http_429_fields(
    monkeypatch,
):
    payload = {
        "error": {
            "message": "Rate limit reached for requests.",
            "type": "rate_limit_error",
            "param": None,
            "code": "rate_limit_exceeded",
        }
    }
    _stub_http_error(
        monkeypatch,
        429,
        json.dumps(payload).encode("utf-8"),
    )
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message == (
        "Model HTTP 429: rate_limit_exceeded — "
        '"Rate limit reached for requests." '
        "(type: rate_limit_error)"
    )
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message


def test_openai_compatible_model_truncates_non_json_http_error(
    monkeypatch,
):
    body = (
        SECRET_KEY.encode("utf-8")
        + b" <html>"
        + (b"rate-limit-page " * 80)
        + b"</html>"
    )
    _stub_http_error(monkeypatch, 400, body)
    with pytest.raises(RuntimeError) as caught:
        _configured_model().analyse(_provider_prompt())
    message = str(caught.value)
    assert message.startswith("Model HTTP 400: ")
    assert SECRET_KEY not in message
    assert "Authorization" not in message
    assert PROMPT_MARKER not in message
    assert len(message) <= len("Model HTTP 400: ") + ERROR_BODY_LIMIT + 3

