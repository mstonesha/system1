"""Reusable synthetic task categories and titles.

Category is encoded in the task title prefix because Task has no
category column. Dataset A must distribute categories evenly enough
that they do not explain the planted temporal pattern.
"""

CATEGORIES = (
    "Engineering",
    "Writing",
    "Research",
    "Admin",
    "Review",
    "Planning",
)

TITLE_STEMS: dict[str, tuple[str, ...]] = {
    "Engineering": (
        "Fix timer DST boundary",
        "Harden session commit path",
        "Tighten daily queue ordering",
        "Repair parent-task completion guard",
        "Improve work-session duration math",
        "Stabilise running-session constraint",
        "Clarify task reopen flow",
        "Reduce duplicate daily-task races",
        "Smooth timer pause handling",
        "Clean up session note validation",
    ),
    "Writing": (
        "Draft weekly review notes",
        "Rewrite execution log intro",
        "Capture decision record",
        "Polish README setup section",
        "Summarise session outcomes",
        "Edit planning memo",
        "Write post-session reflection",
        "Tighten status update",
        "Draft operator checklist",
        "Clarify naming conventions",
    ),
    "Research": (
        "Compare queue-ordering options",
        "Read timezone edge cases",
        "Survey session-length defaults",
        "Investigate abandoned-task rates",
        "Map task hierarchy patterns",
        "Review prior week outcomes",
        "Study interruption clusters",
        "Check calendar-week coverage",
        "Examine duration outliers",
        "Trace stuck-session causes",
    ),
    "Admin": (
        "File receipts for the week",
        "Update operator settings",
        "Tidy archived task titles",
        "Reconcile planned sessions",
        "Clear stale daily-queue items",
        "Backup local notes",
        "Rotate scratch documents",
        "Check dependency pins",
        "Confirm timezone setting",
        "Prune completed subtasks",
    ),
    "Review": (
        "Review yesterday's sessions",
        "Inspect completed-task trail",
        "Audit abandoned work",
        "Check subtask completion order",
        "Re-read stuck session notes",
        "Compare planned vs actual",
        "Scan late-day outcomes",
        "Verify queue order",
        "Look back over the week",
        "Assess lingering tasks",
    ),
    "Planning": (
        "Choose tomorrow's queue",
        "Break down a parent task",
        "Estimate remaining sessions",
        "Set the next milestone",
        "Sequence the week's work",
        "Pick a starting task",
        "Define done-for-now",
        "Schedule a deep-work block",
        "List open questions",
        "Prioritise lingering items",
    ),
}

# Dataset B uses a wider category set. Dataset A keeps CATEGORIES /
# TITLE_STEMS above so its generated mix does not change.
DEPENDENCY_CATEGORIES = (
    "HR",
    "Engineering",
    "Writing",
    "Research",
    "Finance",
    "Admin",
    "Planning",
)

DEPENDENCY_TITLE_STEMS: dict[str, tuple[str, ...]] = {
    "HR": (
        "Confirm recruitment approval",
        "Await contract amendment",
        "Clarify salary-band decision",
        "Chase reference response",
        "Wait for offer-letter confirmation",
        "Follow up on visa approval",
        "Request headcount sign-off",
        "Seek policy-exception decision",
        "Pursue contract-redline response",
        "Confirm start-date approval",
        "Await interview-panel availability",
        "Get salary-band confirmation",
        "Follow up on background-check result",
        "Request relocation-policy decision",
        "Check onboarding-access confirmation",
        "Await hiring-manager response",
    ),
    "Engineering": (
        "Repair reporting query",
        "Fix timer DST boundary",
        "Harden session commit path",
        "Tighten daily queue ordering",
        "Stabilise running-session constraint",
        "Reduce duplicate daily-task races",
        "Smooth timer pause handling",
        "Clean up session note validation",
        "Patch export rounding error",
        "Restore failed migration note",
    ),
    "Writing": (
        "Draft board update",
        "Rewrite execution log intro",
        "Capture decision record",
        "Polish README setup section",
        "Summarise session outcomes",
        "Edit planning memo",
        "Write post-session reflection",
        "Tighten status update",
        "Draft operator checklist",
        "Clarify naming conventions",
    ),
    "Research": (
        "Compare queue-ordering options",
        "Read timezone edge cases",
        "Survey session-length defaults",
        "Investigate abandoned-task rates",
        "Map task hierarchy patterns",
        "Review prior week outcomes",
        "Study interruption clusters",
        "Check calendar-week coverage",
        "Examine duration outliers",
        "Trace stuck-session causes",
    ),
    "Finance": (
        "Reconcile programme budget",
        "Update cost-centre codes",
        "Check monthly spend variance",
        "Prepare forecast revision",
        "File expense backup",
        "Review supplier payment run",
        "Close the weekly cash log",
        "Align budget line items",
        "Post journal corrections",
        "Tally remaining contingency",
    ),
    "Admin": (
        "File receipts for the week",
        "Update operator settings",
        "Tidy archived task titles",
        "Reconcile planned sessions",
        "Clear stale daily-queue items",
        "Backup local notes",
        "Rotate scratch documents",
        "Check dependency pins",
        "Confirm timezone setting",
        "Prune completed subtasks",
    ),
    "Planning": (
        "Choose tomorrow's queue",
        "Break down a parent task",
        "Estimate remaining sessions",
        "Set the next milestone",
        "Sequence the week's work",
        "Pick a starting task",
        "Define done-for-now",
        "Schedule a deep-work block",
        "List open questions",
        "Prioritise lingering items",
    ),
}


def format_task_title(category: str, stem: str, suffix: str | None = None) -> str:
    title = f"[{category}] {stem}"
    if suffix:
        title = f"{title} {suffix}"
    return title


def parse_task_category(title: str) -> str | None:
    if not title.startswith("["):
        return None
    end = title.find("]")
    if end <= 1:
        return None
    return title[1:end]
