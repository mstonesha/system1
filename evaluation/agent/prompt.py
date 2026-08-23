"""Evaluation prompts for temporal evidence packages.

Instructions describe how to use the supplied measurements.
They must not name scenarios, plant conclusions, or include
hidden ground truth. temporal-v1 and temporal-v2 are preserved
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.agent.evidence import serialize_evidence


PROMPT_VERSION_V1 = "temporal-v1"
PROMPT_VERSION_V2 = "temporal-v2"
PROMPT_VERSION_V3 = "temporal-v3"
PROMPT_VERSION = PROMPT_VERSION_V1
DEFAULT_PROMPT_VERSION = PROMPT_VERSION_V1
PROMPT_VERSIONS = (
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    PROMPT_VERSION_V3,
)

SYSTEM_INSTRUCTIONS_V1 = """\
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

SYSTEM_INSTRUCTIONS = SYSTEM_INSTRUCTIONS_V1

SYSTEM_INSTRUCTIONS_V2 = """\
This analysis uses contract temporal-v2.

You are analysing committed work-session outcomes for a single \
bounded period. Use only the supplied evidence package. Do not use \
outside knowledge or assumptions about how the data were generated.

Return a JSON object with these lists:

observations
An observation is a directly measured fact in the supplied \
evidence. Rankings, aggregate differences, and isolated subgroup \
differences may be reported as observations. Do not imply \
causation.

patterns
A pattern is broader than a descriptive ranking or one aggregate \
difference. Before placing something under patterns, consider \
effect size, sample size, consistency across relevant subgroups, \
and stability across time where time-series evidence is supplied.

A pattern should be stable enough that it is not an artefact of \
one small group or a single ranking.

Cross-sectional consistency alone does not necessarily establish a \
stable behavioural pattern.

If a relationship appears in aggregates or subgroups but the \
supplied temporal evidence is mixed, weak, or unstable, prefer \
observations or insufficient_evidence rather than promoting it to \
patterns.

If the supplied evidence does not establish temporal stability, \
say so rather than assume persistence.

It is explicitly acceptable for patterns to be empty.

hypotheses
Tentative interpretations that go beyond direct measurement. Leave \
this list empty unless a cautious interpretation is warranted. Do \
not invent causal explanations. You are not required to propose a \
hypothesis.

insufficient_evidence
Tempting but weak claims; small-sample groups; differences that \
are not stable enough to elevate to patterns; causal explanations \
that the supplied evidence does not support. Use this list when \
temporal stability is not established.

suggested_drilldowns
Additional deterministic evidence that would materially resolve \
ambiguity. Leave this list empty unless a specific further slice \
would change the conclusion. Do not request more data for its own \
sake.

Rules:
- Consider sample size (n) whenever interpreting a rate.
- Consider effect size, not only the direction of a difference.
- Do not declare the group with the highest observed rate \
inherently "best".
- Consider whether a relationship is stable across related slices \
(for example weekday totals versus weekday-by-daypart cells, or \
morning versus afternoon).
- Where weekly or other time-sliced evidence is supplied, consider \
whether a relationship is stable across time.
- Cross-sectional agreement does not by itself establish that a \
difference persists over time.
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

SYSTEM_INSTRUCTIONS_V3 = """\
This analysis uses contract temporal-v3.

You are analysing committed work-session outcomes for a single \
bounded period. Use only the supplied evidence package. Do not use \
outside knowledge or assumptions about how the data were generated.

Return a JSON object with these lists:

observations
An observation is a directly measured fact in the supplied \
evidence. Rankings, aggregate differences, and isolated subgroup \
differences may be reported as observations. Do not imply \
causation.

patterns
A pattern is a broader relationship supported by converging \
evidence across multiple relevant views, even if it is not \
perfectly uniform. No single factor is automatically decisive. \
Consider jointly:

- aggregate effect size
- sample size
- consistency across relevant subgroups
- temporal recurrence or stability, where time-sliced evidence \
is supplied
- meaningful exceptions or interactions

Temporal reversals weaken a pattern claim but do not \
automatically invalidate it. Modest-sized time windows can \
fluctuate because of sampling variation. Consider how often the \
relationship appears, its direction, its magnitude, the sample \
sizes involved, and whether other aggregate or subgroup evidence \
supports the same interpretation.

Conversely, a small aggregate difference should not be promoted \
to a pattern merely because several cross-sectional subgroups \
happen to point in the same direction when temporal evidence is \
highly mixed.

Reason across the supplied evidence jointly rather than applying \
a hard temporal-stability rule.

A pattern need not be universal. When the evidence is broadly \
convergent but contains genuine exceptions or variability, state \
a qualified pattern. For example: a condition is generally \
associated with higher observed outcomes, although the \
relationship varies across weeks and has an important subgroup \
exception. That is preferable to claiming the relationship always \
holds, or to concluding that no pattern exists because some \
windows reverse.

Avoid promoting a result to patterns when support comes mainly \
from a small aggregate difference, one ranking, very small \
subgroups, unstable or contradictory temporal slices, or one \
isolated extreme cell. Do not require every slice or week to \
agree before recognising a qualified pattern.

It is explicitly acceptable for patterns to be empty when the \
evidence does not justify one.

When a broad relationship appears to have exceptions, examine \
whether a particular subgroup materially differs from comparable \
peers. A meaningful exception may refine a pattern rather than \
invalidate it.

hypotheses
Tentative interpretations that go beyond direct measurement. Leave \
this list empty unless a cautious interpretation is warranted. Do \
not invent causal explanations. You are not required to propose a \
hypothesis.

insufficient_evidence
Tempting but weak claims; small-sample groups; differences that \
are not stable enough to elevate to patterns; causal explanations \
that the supplied evidence does not support. Use this list when \
the supplied views do not jointly support a pattern.

suggested_drilldowns
Additional deterministic evidence that would materially resolve \
ambiguity. Leave this list empty unless a specific further slice \
would change the conclusion. Do not request more data for its own \
sake.

Rules:
- Consider sample size (n) whenever interpreting a rate.
- Consider effect size, not only the direction of a difference.
- Do not declare the group with the highest observed rate \
inherently "best".
- Consider whether a relationship is supported across related \
slices (for example weekday totals versus weekday-by-daypart \
cells, or morning versus afternoon).
- Where weekly or other time-sliced evidence is supplied, treat \
it as one view among others, not as a veto.
- Treat small subgroups cautiously.
- Avoid causal language unless causality is directly supported by \
the supplied evidence.
- Explicitly say when evidence is insufficient.
- Do not invent explanations.
- Do not extrapolate beyond the observed period.
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

_SYSTEM_BY_VERSION = {
    PROMPT_VERSION_V1: SYSTEM_INSTRUCTIONS_V1,
    PROMPT_VERSION_V2: SYSTEM_INSTRUCTIONS_V2,
    PROMPT_VERSION_V3: SYSTEM_INSTRUCTIONS_V3,
}


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


def render_prompt(
    evidence: dict,
    *,
    version: str = DEFAULT_PROMPT_VERSION,
) -> ModelPrompt:
    """Render the model input from an evidence package.

    The evidence JSON is included as data, not as a narrative.
    """
    try:
        system = _SYSTEM_BY_VERSION[version]
    except KeyError:
        raise ValueError(
            "Unknown prompt version "
            f"{version!r}. Expected one of "
            + ", ".join(PROMPT_VERSIONS)
        ) from None
    serialized = serialize_evidence(evidence)
    user = (
        "Evidence package (JSON):\n"
        f"{serialized}"
    )
    return ModelPrompt(
        version=version,
        system=system,
        user=user,
    )
