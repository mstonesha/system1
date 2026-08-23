"""Scenario and prompt-contract compatibility.

The model never sees these names. They exist so the CLI cannot
pair a temporal prompt with dependency evidence, or the reverse.
"""

from __future__ import annotations


SCENARIO_CASE_IDS = {
    "temporal_patterns": "case_a",
    "noise_control": "case_f",
    "interruptions_dependencies": "case_b",
    "task_age_abandonment": "case_c",
}
TEMPORAL_VERSIONS = frozenset(
    {"temporal-v1", "temporal-v2", "temporal-v3"}
)
DEPENDENCY_VERSIONS = frozenset({"dependencies-v1"})
TASK_AGE_VERSIONS = frozenset({"task-age-v1"})
CASE_CONTRACTS = {
    "case_a": TEMPORAL_VERSIONS,
    "case_f": TEMPORAL_VERSIONS,
    "case_b": DEPENDENCY_VERSIONS,
    "case_c": TASK_AGE_VERSIONS,
}


def require_compatible(
    scenario: str,
    prompt_version: str,
) -> str:
    """Return the opaque case_id if the pair is valid."""
    try:
        case_id = SCENARIO_CASE_IDS[scenario]
    except KeyError:
        raise ValueError(
            f"Unknown evaluation scenario {scenario!r}."
        ) from None
    allowed = CASE_CONTRACTS[case_id]
    if prompt_version not in allowed:
        raise ValueError(
            f"Prompt version {prompt_version!r} is not "
            f"compatible with scenario {scenario!r} "
            f"(opaque {case_id}). Use one of: "
            + ", ".join(sorted(allowed))
        )
    return case_id


def require_case_contract(
    case_id: str,
    prompt_version: str,
) -> None:
    allowed = CASE_CONTRACTS.get(case_id)
    if allowed is None:
        raise ValueError(
            f"Unknown opaque case_id {case_id!r}."
        )
    if prompt_version not in allowed:
        raise ValueError(
            f"Prompt version {prompt_version!r} is not "
            f"compatible with {case_id}. Use one of: "
            + ", ".join(sorted(allowed))
        )
