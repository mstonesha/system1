# Agent evaluation

The evaluation system tests whether an analytical model can correctly
interpret bounded evidence produced by production analytics.

The question is not “can the model produce plausible analysis?” It is
whether the model can distinguish conclusions the evidence actually
supports from tempting but unsupported ones.

This is a synthetic harness. Passing it is not proof of general
analytical reliability. Enkrateia_One is not built yet.

## Purpose

Each case plants a known structure in a deterministic dataset, runs
that data through the real schema and `app/analytics/`, then asks a
model to interpret only the evidence contract for that case.

The operator compares the structured JSON result to hidden ground
truth. Ground truth is never included in the prompt.

## Evaluation architecture

```
deterministic synthetic dataset
  → real schema (reset eval database)
  → production analytics
  → evidence contract
  → model prompt
  → structured JSON result
  → operator comparison against hidden ground truth
```

Synthetic scenarios use the real application schema and production
analytics. Generation writes only to `system1_agent_eval`. The
generator refuses `system1` and `system1_test`.

CLI:

```bash
python -m evaluation.generate temporal_patterns
python -m evaluation.agent.runner --scenario temporal_patterns --dry-run
python -m evaluation.agent.runner --scenario temporal_patterns --prompt-version temporal-v3
```

Scenario and prompt version must be compatible
(`evaluation/agent/contracts.py`). The model never sees scenario
names; it receives an opaque `case_id`.

Live runs need `EVAL_AGENT_API_KEY`, `EVAL_AGENT_BASE_URL`, and
`EVAL_AGENT_MODEL`. The client is a thin OpenAI-compatible Chat
Completions caller. No vendor is assumed. Tests use
`StubAnalysisModel` and must not perform network calls.

Results under `evaluation/agent/results/` are gitignored. This
repository does not contain recorded live-run metadata.

## Generator truth vs agent-evaluable truth

Generator truth may include facts about how the synthetic scenario
was constructed: hidden category labels, planted week-regime names,
phase boundaries, decoy-week identities, or rates that are only
visible if you query columns the evidence contract does not expose.

The agent must be judged only against facts inferable from the
evidence package it actually received.

Dataset B (`interruptions_dependencies`) made this concrete.
The generator plants HR/non-HR stuck-rate differences and knows
that HR interruption rates are not materially higher. The
`dependencies-v1` contract does not expose structured HR
categories, session denominators, or HR-wide stuck rates. It
exposes interruption aggregates, a bounded stuck-task drilldown,
and task titles as ordinary language.

Scoring the model against generator-only HR rates would punish
correct restraint and reward leakage. Ground truth files therefore
split `generator_truth` / `not_agent_evaluable` from
`agent_expected_*` fields. `evaluation/__init__.py` states the same
rule.

## Reasoning taxonomy

The model must return JSON with these lists
(`evaluation/agent/types.py`):

| Field | Meaning |
|---|---|
| `observations` | Directly measured facts: rankings, counts, isolated subgroup differences. No causation. |
| `patterns` | Broader relationships supported by converging evidence, not one ranking or one small cell. Qualified patterns are allowed. Empty is allowed. |
| `hypotheses` | Tentative interpretations beyond the measurements. Optional. Not established causes. |
| `insufficient_evidence` | Tempting but weak claims; small samples; unsupported causation; representative claims a bounded drilldown cannot justify. |
| `suggested_drilldowns` | Further deterministic slices that would materially change the conclusion. Not arbitrary SQL. |

Association is not causation. Absence of evidence for a pattern is
an acceptable result. Not every question has an interesting answer.

## Converging-evidence principle

Developed while iterating temporal contracts, then reused in later
prompts.

Weigh together:

- effect size
- sample size
- subgroup consistency
- temporal recurrence (when time-sliced evidence is supplied)
- exceptions
- competing windows

No single factor is automatically decisive. Reversals weaken a
claim but do not automatically veto a qualified pattern. A few
same-direction subgroups do not automatically establish a pattern
if the broader temporal evidence is unstable.

`temporal-v3` states this explicitly. Later contracts
(`dependencies-v1`, `task-age-v1`, `planning-v1`, `change-v1`)
adapt the same discipline to their evidence families.

## Evidence contracts

Frozen / active prompt-and-evidence versions and the scenarios they
may pair with:

| Contract | Scenarios | Evidence |
|---|---|---|
| `temporal-v1` | `temporal_patterns` (A), `noise_control` (F) | Weekday, daypart, weekday×daypart, morning/afternoon aggregates. No weekly series. |
| `temporal-v2` | A, F | Same as v1 plus weekly morning/afternoon rows. |
| `temporal-v3` | A, F | Same evidence package as v2; revised pattern-reasoning instructions. Current temporal contract. |
| `dependencies-v1` | `interruptions_dependencies` (B) | Interruption outcomes and bounded stuck-task drilldown (titles included, unparsed). |
| `task-age-v1` | `task_age_abandonment` (C) | Execution-age abandonment buckets and datable terminal-task rows. No titles. No `created_at` age. |
| `planning-v1` | `planning_workload` (D) | Daily planning capacity, completed-task effort estimates, weekly planned-session rows. |
| `change-v1` | `behaviour_change` (E) | Full-period, rolling recent 8w/16w, preceding 16w, window comparison, weekly morning/afternoon. |

Older temporal contracts are retained because they record evaluation
history. `temporal-v3` is the current successful temporal contract
in the sense that the harness is designed so A and F can both be
answered correctly under converging-evidence rules. This repository
does not store live model pass/fail artefacts.

## Scenario catalogue

| Scenario | Dataset | Contract | Primary reasoning challenge | What success looks like |
|---|---|---|---|---|
| `temporal_patterns` | A | `temporal-v3` (also v1/v2 for history) | Detect a genuine morning-over-afternoon pattern with a meaningful Monday-morning exception | Qualified pattern plus exception; no causation; small early/evening samples treated cautiously |
| `interruptions_dependencies` | B | `dependencies-v1` | Aggregate interruption association; semantic concentration in a bounded stuck drilldown; keep those observations separate | Association, not causation; titles may support a hypothesis; drilldown is not a denominator |
| `task_age_abandonment` | C | `task-age-v1` | Graded execution-age association without treating it as determinism or as `created_at` age | Population association; old tasks can complete; young tasks can be abandoned |
| `planning_workload` | D | `planning-v1` | Keep daily planning, task effort estimation, and workload/outcome association separate | Unused capacity decomposed; early completion ≠ planning failure; planned sessions ≠ DailyTask count |
| `behaviour_change` | E | `change-v1` | Historical morning advantage vs current behaviour; weakening without requiring reversal | Lifetime aggregate may be stale; one strong week is not a new regime |
| `noise_control` | F | `temporal-v3` (also v1/v2) | Reject plausible but non-robust patterns | Rankings as observations are allowed; patterns empty or explicitly unsupported |

Opaque case ids: A `case_a`, B `case_b`, C `case_c`, D `case_d`,
E `case_e`, F `case_f`.

## Temporal contract evolution

| Contract | A (`temporal_patterns`) | F (`noise_control`) | Lesson |
|---|---|---|---|
| `temporal-v1` | intended pass | intended fail — too permissive | Found real signal but over-interpreted noise |
| `temporal-v2` | intended fail — too conservative | intended pass | Suppressed noise and also suppressed valid qualified patterns |
| `temporal-v3` | intended pass | intended pass | Converging-evidence reasoning |

This is experimental history encoded in frozen prompts, not
statistical proof and not a claim of formal optimality. Live model
outputs for those iterations are not checked into the repository.

## Contract-specific lessons

### `dependencies-v1`

- Bounded drilldown is frequency- and recency-biased. It is not a
  representative denominator.
- Semantic task titles may support a *hypothesis* about waiting or
  dependencies. Titles are not verified categories.
- Aggregate interruption evidence and semantic dependency evidence
  must not be causally linked unless the package actually joins
  them.

### `task-age-v1`

- Use execution age (first non-removed Today appearance → terminal
  date), not `Task.created_at`.
- A monotonic bucket association is not determinism.
- Old tasks can still complete; young tasks can still be abandoned.
- List-only cancellation cannot be dated and is excluded.

### `planning-v1`

- Planned DailyTask capacity is distinct from task
  `estimated_sessions` accuracy.
- Unused planned sessions have multiple meanings (unfinished, early
  completion, abandonment).
- Early completion is not equivalent to planning failure.
- Planned sessions are more informative for workload than DailyTask
  count alone.
- Workload/outcome association is not causation.
- There is no universal overload threshold in the evidence.

### `change-v1`

- Lifetime aggregates may become stale.
- Recent windows must be weighed against larger history.
- Disappearance of an old pattern does not require establishing a
  reversed pattern.
- Isolated strong weeks must not define a new regime.
- Morning is 09:00–11:59; afternoon is 12:00–17:59.

## Prompt/hash freezing

Successful and historically important system prompts are hashed in
`tests/test_evaluation_agent.py` (`FROZEN_PROMPT_SHA256`) so later
edits cannot silently change earlier contracts.

Current frozen SHA-256 digests of the system-instruction strings:

| Key | SHA-256 |
|---|---|
| `temporal-v1` | `dae441068655e1c0388afb78ae3b4096a944fb8f9046c85c3243f0db8b90770c` |
| `temporal-v2` | `9be5ced2c7c1dcae87d161478c626fbdbe02f1b3bf690b796ef0cda568d06eb8` |
| `temporal-v3` | `926173fbfbc318d67bffc9fa084bdf3df319b99fc9765ab03c73089ff2d99ce8` |
| `dependencies-v1` | `b6d6085322266708abfa52231ef55ae3c589763a37138679521620e7991b804d` |
| `task-age-v1` | `a30e903d4f9ded5a4c05bd8d3f44b8488071f0bd9e8ee3be85941ef671f82546` |
| `planning-v1` | `3d7b141d8f77f7f3cd40668b6724c40dde153be986546916b4dc9b615ad3d100` |
| `change-v1` | `8a64d729f74c3864d5f71dbef83db597383fde88b621906a9f8d2bc0b9bd5b7e` |

If a prompt must change, that is a new contract version plus a new
frozen hash, not a silent edit of an old string.

## Current limitations

- Evaluation is synthetic.
- Passing synthetic scenarios is not proof of general analytical
  reliability.
- No statistical significance testing in the harness.
- No LLM-as-judge.
- No automatic semantic scoring of free-text statements.
- Result interpretation still includes human review against hidden
  YAML.
- Production Enkrateia_One is not built.
- Live run JSON is gitignored and not part of this snapshot.
