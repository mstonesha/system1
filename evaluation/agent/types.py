"""Structured agent-analysis types for the evaluation harness."""

from __future__ import annotations

import json
from dataclasses import dataclass


REQUIRED_TOP_LEVEL = (
    "observations",
    "patterns",
    "hypotheses",
    "insufficient_evidence",
    "suggested_drilldowns",
)
STATEMENT_LIST_FIELDS = (
    "observations",
    "patterns",
    "hypotheses",
    "insufficient_evidence",
)


@dataclass(frozen=True)
class EvidenceBackedStatement:
    statement: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class AgentAnalysis:
    observations: tuple[EvidenceBackedStatement, ...]
    patterns: tuple[EvidenceBackedStatement, ...]
    hypotheses: tuple[EvidenceBackedStatement, ...]
    insufficient_evidence: tuple[EvidenceBackedStatement, ...]
    suggested_drilldowns: tuple[EvidenceBackedStatement, ...]

    def to_dict(self) -> dict:
        return {
            "observations": [
                _statement_to_dict(item)
                for item in self.observations
            ],
            "patterns": [
                _statement_to_dict(item)
                for item in self.patterns
            ],
            "hypotheses": [
                _statement_to_dict(item)
                for item in self.hypotheses
            ],
            "insufficient_evidence": [
                _statement_to_dict(item)
                for item in self.insufficient_evidence
            ],
            "suggested_drilldowns": [
                _statement_to_dict(item)
                for item in self.suggested_drilldowns
            ],
        }


@dataclass(frozen=True)
class ParsedAgentResponse:
    status: str
    raw_text: str
    analysis: AgentAnalysis | None
    errors: tuple[str, ...]
    unknown_evidence_refs: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def parse_agent_analysis(
    raw_text: str,
    *,
    known_ids: set[str] | None = None,
) -> ParsedAgentResponse:
    """Parse a model response as AgentAnalysis JSON.

    Malformed JSON and missing required fields fail. This does
    not strip markdown fences, coerce types, or invent missing
    lists.
    """
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return ParsedAgentResponse(
            status="invalid_json",
            raw_text=raw_text,
            analysis=None,
            errors=(f"JSON parse failed: {exc.msg}",),
            unknown_evidence_refs=(),
        )

    if not isinstance(payload, dict):
        return ParsedAgentResponse(
            status="invalid_schema",
            raw_text=raw_text,
            analysis=None,
            errors=("Top-level JSON must be an object.",),
            unknown_evidence_refs=(),
        )

    errors = _schema_errors(payload)
    if errors:
        return ParsedAgentResponse(
            status="invalid_schema",
            raw_text=raw_text,
            analysis=None,
            errors=tuple(errors),
            unknown_evidence_refs=(),
        )

    analysis = AgentAnalysis(
        observations=_parse_statements(payload["observations"]),
        patterns=_parse_statements(payload["patterns"]),
        hypotheses=_parse_statements(payload["hypotheses"]),
        insufficient_evidence=_parse_statements(
            payload["insufficient_evidence"]
        ),
        suggested_drilldowns=_parse_statements(
            payload["suggested_drilldowns"],
            refs_required=False,
        ),
    )
    unknown = _unknown_refs(analysis, known_ids)
    return ParsedAgentResponse(
        status="ok",
        raw_text=raw_text,
        analysis=analysis,
        errors=(),
        unknown_evidence_refs=unknown,
    )


def _schema_errors(payload: dict) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_TOP_LEVEL:
        if field not in payload:
            errors.append(f"Missing required field: {field}")
    if errors:
        return errors
    for field in REQUIRED_TOP_LEVEL:
        value = payload[field]
        if not isinstance(value, list):
            errors.append(f"{field} must be a list.")
            continue
        refs_required = field in STATEMENT_LIST_FIELDS
        for index, item in enumerate(value):
            errors.extend(
                _item_errors(
                    field,
                    index,
                    item,
                    refs_required=refs_required,
                )
            )
    return errors


def _item_errors(
    field: str,
    index: int,
    item: object,
    *,
    refs_required: bool,
) -> list[str]:
    prefix = f"{field}[{index}]"
    if not isinstance(item, dict):
        return [f"{prefix} must be an object."]
    errors: list[str] = []
    statement = item.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        errors.append(f"{prefix}.statement must be a non-empty string.")
    if refs_required or "evidence_refs" in item:
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) for ref in refs
        ):
            errors.append(
                f"{prefix}.evidence_refs must be a list of strings."
            )
    return errors


def _parse_statements(
    items: list,
    *,
    refs_required: bool = True,
) -> tuple[EvidenceBackedStatement, ...]:
    parsed: list[EvidenceBackedStatement] = []
    for item in items:
        refs = item.get("evidence_refs", [])
        if not refs_required and "evidence_refs" not in item:
            refs = []
        parsed.append(
            EvidenceBackedStatement(
                statement=item["statement"],
                evidence_refs=tuple(refs),
            )
        )
    return tuple(parsed)


def _unknown_refs(
    analysis: AgentAnalysis,
    known_ids: set[str] | None,
) -> tuple[str, ...]:
    if known_ids is None:
        return ()
    seen: list[str] = []
    for collection in (
        analysis.observations,
        analysis.patterns,
        analysis.hypotheses,
        analysis.insufficient_evidence,
        analysis.suggested_drilldowns,
    ):
        for item in collection:
            for ref in item.evidence_refs:
                if ref not in known_ids and ref not in seen:
                    seen.append(ref)
    return tuple(seen)


def _statement_to_dict(item: EvidenceBackedStatement) -> dict:
    return {
        "statement": item.statement,
        "evidence_refs": list(item.evidence_refs),
    }
