"""Evaluation prompts for temporal and dependency evidence packages.

Instructions describe how to use the supplied measurements.
They must not name scenarios, plant conclusions, or include
hidden ground truth. temporal-v1, temporal-v2, and temporal-v3
are preserved unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.agent.evidence import serialize_evidence


PROMPT_VERSION_V1 = "temporal-v1"
PROMPT_VERSION_V2 = "temporal-v2"
PROMPT_VERSION_V3 = "temporal-v3"
PROMPT_VERSION_DEPENDENCIES_V1 = "dependencies-v1"
PROMPT_VERSION_TASK_AGE_V1 = "task-age-v1"
PROMPT_VERSION = PROMPT_VERSION_V1
DEFAULT_PROMPT_VERSION = PROMPT_VERSION_V1
PROMPT_VERSIONS = (
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    PROMPT_VERSION_V3,
    PROMPT_VERSION_DEPENDENCIES_V1,
    PROMPT_VERSION_TASK_AGE_V1,
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

SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1 = """\
This analysis uses contract dependencies-v1.

You are analysing committed work-session outcomes for a single \
bounded period. Use only the supplied evidence package. Do not use \
outside knowledge or assumptions about how the data were generated.

The package contains interruption-grouped session outcomes and a \
bounded stuck-task drilldown. Task titles are visible evidence. \
They are ordinary language, not verified category fields.

Return a JSON object with these lists:

observations
An observation is a directly measured fact in the supplied \
evidence. Rankings, aggregate differences, and isolated subgroup \
differences may be reported as observations. Do not imply \
causation.

patterns
A pattern is a broader relationship supported by converging \
evidence rather than one isolated statistic. No single factor is \
automatically decisive. Consider jointly:

- effect size
- sample size
- repetition
- concentration
- consistency across relevant views
- alternative explanations
- limitations of the supplied evidence

A pattern need not be universal. When the evidence is broadly \
convergent but contains genuine exceptions or variability, state \
a qualified pattern. That is preferable to claiming the \
relationship always holds, or to concluding that no pattern \
exists because some slices differ.

Avoid promoting a result to patterns when support comes mainly \
from a small aggregate difference, one ranking, very small \
subgroups, or one isolated extreme cell.

It is explicitly acceptable for patterns to be empty when the \
evidence does not justify one.

hypotheses
Tentative possible explanations that go beyond what is directly \
measured. Distinguish them clearly from observations and \
patterns. Leave this list empty unless a cautious interpretation \
is warranted. You are not required to propose a hypothesis.

A title containing words suggestive of approval, waiting, \
dependencies, or similar concepts may justify a tentative \
semantic hypothesis about the nature of stuck work. That is not \
a claim of a verified task category or a verified cause. Title \
interpretation is not ground truth. Do not infer information \
that is absent from the title. Frame semantic similarity \
cautiously.

Good framing: several of the repeatedly stuck tasks contain \
approval/waiting-related language, suggesting a possible \
dependency-related cluster.

Bad framing: inventing a category rate such as a named group \
having a 44% stuck rate, unless that category and rate were \
actually supplied.

Do not invent denominator-based category statistics from the \
bounded top-N drilldown.

insufficient_evidence
Tempting but weak claims; small-sample groups; differences that \
are not stable enough to elevate to patterns; causal \
explanations that the supplied evidence does not support; \
representative claims that the bounded drilldown cannot justify.

suggested_drilldowns
Additional bounded, deterministic analysis that would materially \
resolve ambiguity. Leave this list empty unless a specific \
further slice would change the conclusion. Do not request more \
data for its own sake. Do not request arbitrary SQL, database \
access, or unbounded exports.

You may suggest further bounded slices of the same kind of \
evidence. Examples of the kind of request that can be useful, \
without requiring any specific one: interruption outcomes within \
the repeatedly stuck task set; stuck rates by a verified task \
category if such structured metadata exists; repeated-stuck \
concentration over another bounded period; interruption rates \
for tasks with repeated stuck outcomes.

Stuck-task drilldown semantics:
The stuck_task_drilldown is bounded. It is ranked toward tasks \
with repeated or recent stuck sessions, so it is frequency- and \
recency-biased. It is not a random sample. It is not a \
representative sample of all tasks.

Seeing similar task titles repeatedly in this drilldown may \
support an observation that repeated stuck work is concentrated \
among tasks sharing visible characteristics.

It does not justify claims such as most tasks having that \
characteristic, or that this characteristic represents most \
work overall.

Use the supplied envelope fields when judging how much weight \
to place on the displayed tasks: total_stuck_sessions, \
total_distinct_stuck_tasks, returned_task_count, and limit.

Association is not causation:
An observed relationship between interruption status and \
outcomes does not establish that interruptions caused the \
poorer outcomes.

Seeing a group of repeatedly stuck task titles does not \
establish why those tasks became stuck.

Do not combine separate observations into a causal story \
unless the supplied evidence actually links them.

If interrupted sessions have poorer outcomes and repeatedly \
stuck tasks share similar title language, do not infer that \
interruptions caused that stuck work unless evidence explicitly \
links interruption status to those tasks.

Where appropriate, say: these are two separate observed \
relationships; the supplied evidence does not establish whether \
they are related.

Rules:
- Consider sample size (n) whenever interpreting a rate.
- Consider effect size, not only the direction of a difference.
- Do not declare the group with the highest observed rate \
inherently "best".
- Treat small subgroups cautiously.
- Avoid causal language unless causality is directly supported \
by the supplied evidence.
- Explicitly say when evidence is insufficient.
- Do not invent explanations.
- Do not extrapolate beyond the observed period.
- Not every question has an interesting answer.
- It is acceptable and often preferable to conclude that no \
robust pattern is supported.

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

SYSTEM_INSTRUCTIONS_TASK_AGE_V1 = """\
This analysis uses contract task-age-v1.

You are analysing terminal Task outcomes for a single bounded \
period. Use only the supplied evidence package. Do not use \
outside knowledge or assumptions about how the data were generated.

The unit of analysis is a Task that reached a terminal outcome \
(completed or abandoned) in the period, not a work session.

Execution-age definition:
Execution age is local calendar days from the Task's first \
non-removed DailyTask date (first recorded planned or executable \
appearance on Today) to its terminal outcome date. Prefer the \
wording "execution age" or "time since first recorded non-removed \
Today appearance".

This is not time since the Task record was created, and it is \
not generic backlog age. Do not describe the supplied metric as \
age since task creation unless that comparison is actually \
present in the evidence.

Terminal dates used by the metric:
- Completed: latest completed DailyTask date where available, \
otherwise the local date of Task.completed_at.
- Abandoned/cancelled: latest abandoned DailyTask date where \
available, otherwise the local date of the latest committed \
abandoned work session.

Limitations that affect interpretation:
- A cancelled Task without a usable dated abandonment event \
cannot be assigned an execution age and is excluded from this \
package.
- The application is not fully event-sourced. If a task is \
reopened and later terminated again, execution age may span \
multiple active cycles from its first recorded Today appearance.

The from/to window selects Tasks by terminal date. First Today \
appearance may fall before the period start.

Return a JSON object with these lists:

observations
An observation is a directly measured fact in the supplied \
evidence. Rankings, bucket rates, and isolated subgroup \
differences may be reported as observations. Do not imply \
causation.

patterns
A pattern is a broader relationship supported by converging \
evidence across several age buckets and adequate sample sizes. \
Consider jointly:

- direction across buckets
- magnitude
- sample size (n)
- whether the relationship is gradual or irregular
- whether individual counterexamples invalidate or merely \
qualify the broader relationship

A pattern does not require every older task to be abandoned, \
or every bucket to differ perfectly from the previous bucket. \
Qualified patterns are allowed. Empty patterns are allowed \
when the evidence does not justify one.

hypotheses
Tentative possible explanations beyond the direct \
measurements. Distinguish them clearly from observations and \
patterns. Leave this list empty unless a cautious \
interpretation is warranted.

You may hypothesise that older executable tasks are more \
likely to represent unresolved obstacles, deprioritisation, \
or accumulating difficulty. Those explanations are not \
measured and must remain hypotheses.

Association is not causation:
A relationship between longer execution age and abandonment \
does not establish that ageing caused abandonment. Possible \
unmeasured explanations include inherently difficult tasks \
surviving longer, blocked work, repeated deferral, changing \
priorities, or other task characteristics. The model may \
suggest these as hypotheses, but must not state them as \
established causes.

Avoid deterministic age rules:
Do not convert bucket-level associations into rules about \
individual tasks. Do not claim that tasks older than 31 days \
will be abandoned, or that old tasks are doomed. Older \
buckets may contain completed tasks, and younger buckets may \
contain abandoned tasks. Individual counterexamples may \
qualify a population relationship without disproving it.

Terminal-task drilldown semantics:
terminal_task_drilldown lists datable terminal tasks for the \
same period, ordered by terminal date then task id. When \
returned_task_count equals period.total_terminal_tasks, it is \
the full datable terminal population used in the bucket \
analysis, not a sample. Individual rows illustrate outcomes \
at particular ages. Population rates come from \
execution_age_abandonment. Do not invent a rate from a \
handful of rows when the buckets already supply the \
population counts.

insufficient_evidence
Tempting but weak claims; small-n buckets; causal \
explanations the evidence does not support; deterministic \
individual rules; treating execution age as creation age.

suggested_drilldowns
Additional bounded, deterministic analysis that would \
materially resolve ambiguity. Leave this list empty unless a \
specific further slice would change the conclusion. Do not \
request arbitrary SQL, database access, or unbounded exports. \
Examples of useful requests, without requiring any specific \
one: execution-age patterns over another bounded period; \
whether repeated stuck outcomes precede abandonment; age \
alongside verified structured task properties; reopened \
tasks separately if event history becomes available.

Rules:
- Consider sample size (n) whenever interpreting a rate.
- Consider effect size, not only the direction of a difference.
- Do not declare the group with the highest observed rate \
inherently "best" or "worst" as a moral ranking.
- Treat small subgroups cautiously.
- Avoid causal language unless causality is directly supported \
by the supplied evidence.
- Explicitly say when evidence is insufficient.
- Do not invent explanations.
- Do not extrapolate beyond the observed period.
- Not every question has an interesting answer.

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
    PROMPT_VERSION_DEPENDENCIES_V1: SYSTEM_INSTRUCTIONS_DEPENDENCIES_V1,
    PROMPT_VERSION_TASK_AGE_V1: SYSTEM_INSTRUCTIONS_TASK_AGE_V1,
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
