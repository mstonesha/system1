"""Evaluation prompt for temporal evidence packages.

Instructions describe how to use the supplied measurements.
They must not name scenarios, plant conclusions, or include
hidden ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.agent.evidence import serialize_evidence


PROMPT_VERSION = "temporal-v1"

SYSTEM_INSTRUCTIONS = """\
You are analysing committed work-session outcomes for a single \
bounded period. Use only the supplied evidence package. Do not use \
outside knowledge or assumptions about how the data were generated.

Return a JSON object with these lists:

observations
Direct descriptive statements supported by the supplied \
measurements. Rankings and numeric differences are allowed. Do not \
imply causation.

patterns
Broader relationships that appear sufficiently supported across \
related slices of the supplied evidence. A pattern should be stable \
enough that it is not an artefact of one small group or a single \
ranking.

hypotheses
Tentative interpretations that go beyond direct measurement. Leave \
this list empty unless a cautious interpretation is warranted. Do \
not invent causal explanations. You are not required to propose a \
hypothesis.

insufficient_evidence
Tempting but weak claims; small-sample groups; differences that \
are not stable enough to elevate to patterns; causal explanations \
that the supplied evidence does not support.

suggested_drilldowns
Additional deterministic evidence that would materially resolve \
ambiguity. Leave this list empty unless a specific further slice \
would change the conclusion. Do not request more data for its own \
sake.

Rules:
- Consider sample size (n) whenever interpreting a rate.
- Do not declare the group with the highest observed rate \
inherently "best".
- Consider whether a relationship is stable across related slices \
(for example weekday totals versus weekday-by-daypart cells, or \
morning versus afternoon).
- Treat small subgroups cautiously.
- Avoid causal language unless causality is directly supported by \
the supplied evidence.
- Explicitly say when evidence is insufficient.
- Do not invent explanations.
- Not every question has an interesting answer.
- It is acceptable and often preferable to conclude that no robust \
pattern is supported.

Where practical, cite evidence ids from the package in \
evidence_refs.

Return JSON only, with no markdown fences, matching:
{
  "observations": [
    {"statement": "...", "evidence_refs": ["..."]}
  ],
  "patterns": [
    {"statement": "...", "evidence_refs": ["..."]}
  ],
  "hypotheses": [
    {"statement": "...", "evidence_refs": ["..."]}
  ],
  "insufficient_evidence": [
    {"statement": "...", "evidence_refs": ["..."]}
  ],
  "suggested_drilldowns": [
    {"statement": "...", "evidence_refs": []}
  ]
}
"""


@dataclass(frozen=True)
class ModelPrompt:
    version: str
    system: str
    user: str

    def combined_text(self) -> str:
        return f"{self.system}\n\n{self.user}"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "system": self.system,
            "user": self.user,
        }


def render_prompt(evidence: dict) -> ModelPrompt:
    """Render the model input from an evidence package.

    The evidence JSON is included as data, not as a narrative.
    """
    serialized = serialize_evidence(evidence)
    user = (
        "Evidence package (JSON):\n"
        f"{serialized}"
    )
    return ModelPrompt(
        version=PROMPT_VERSION,
        system=SYSTEM_INSTRUCTIONS,
        user=user,
    )
