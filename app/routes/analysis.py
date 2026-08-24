"""Read-only HTTP transport for production analytics.

These routes call ``app.analysis`` only. They do not interpret
results, open engines, or write application rows.

The API is unauthenticated and is not safe to expose on the
public internet.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analysis import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    get_change_summary,
    get_interruption_summary,
    get_planning_summary,
    get_stuck_task_drilldown,
    get_task_age_summary,
    get_temporal_summary,
    get_terminal_task_age_drilldown,
)
from app.api.analysis_models import (
    ChangeSummaryResponse,
    InterruptionSummaryResponse,
    PlanningSummaryResponse,
    StuckTaskSummaryResponse,
    TaskAgeSummaryResponse,
    TemporalSummaryResponse,
    TerminalTaskAgeDrilldownResponse,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/analysis",
    tags=["Analysis"],
)


def analysis_period(
    from_date: date = Query(
        ...,
        description=(
            "Inclusive local start date (YYYY-MM-DD). "
            "Timezone is server-controlled."
        ),
    ),
    to_date: date = Query(
        ...,
        description=(
            "Inclusive local end date (YYYY-MM-DD). "
            "Timezone is server-controlled."
        ),
    ),
) -> tuple[date, date]:
    if from_date > to_date:
        raise HTTPException(
            status_code=400,
            detail=(
                "from_date must be on or before to_date."
            ),
        )
    return from_date, to_date


def drilldown_limit(
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=MIN_LIMIT,
        le=MAX_LIMIT,
        description=(
            "Maximum rows to return "
            f"({MIN_LIMIT}–{MAX_LIMIT})."
        ),
    ),
) -> int:
    return limit


def _call(func, db: Session, **kwargs):
    try:
        return func(db, **kwargs)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get(
    "/temporal",
    response_model=TemporalSummaryResponse,
    summary="Temporal session outcomes",
)
def read_temporal(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_temporal_summary,
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return TemporalSummaryResponse.model_validate(result)


@router.get(
    "/interruptions",
    response_model=InterruptionSummaryResponse,
    summary="Outcomes by interruption status",
)
def read_interruptions(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_interruption_summary,
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return InterruptionSummaryResponse.model_validate(
        result
    )


@router.get(
    "/task-age",
    response_model=TaskAgeSummaryResponse,
    summary="Execution-age abandonment buckets",
)
def read_task_age(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_task_age_summary,
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return TaskAgeSummaryResponse.model_validate(result)


@router.get(
    "/task-age/drilldown",
    response_model=TerminalTaskAgeDrilldownResponse,
    summary="Bounded terminal-task execution-age rows",
)
def read_task_age_drilldown(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    limit: Annotated[int, Depends(drilldown_limit)],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_terminal_task_age_drilldown,
        db,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return TerminalTaskAgeDrilldownResponse.model_validate(
        result
    )


@router.get(
    "/planning",
    response_model=PlanningSummaryResponse,
    summary="Daily planning, effort, and weekly workload",
)
def read_planning(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_planning_summary,
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return PlanningSummaryResponse.model_validate(result)


@router.get(
    "/change",
    response_model=ChangeSummaryResponse,
    summary="Morning/afternoon windows and weekly series",
)
def read_change(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_change_summary,
        db,
        from_date=from_date,
        to_date=to_date,
    )
    return ChangeSummaryResponse.model_validate(result)


@router.get(
    "/stuck-tasks",
    response_model=StuckTaskSummaryResponse,
    summary="Bounded stuck-task drill-down",
)
def read_stuck_tasks(
    period: Annotated[
        tuple[date, date],
        Depends(analysis_period),
    ],
    limit: Annotated[int, Depends(drilldown_limit)],
    db: Session = Depends(get_db),
):
    from_date, to_date = period
    result = _call(
        get_stuck_task_drilldown,
        db,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )
    return StuckTaskSummaryResponse.model_validate(result)
