from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.review import build_daily_review
from app.templating import templates
from app.time import today

router = APIRouter(
    prefix="/review",
    tags=["review"],
)


@router.get("/")
def review_page(
    request: Request,
    target_date: date | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or today()

    previous_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    daily_review = build_daily_review(
        db=db,
        target_date=selected_date,
    )

    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "selected_date": selected_date,
            "previous_date": previous_date,
            "next_date": next_date,
            "sessions": daily_review["sessions"],
            "summary": daily_review["summary"],
            "task_performance": daily_review["task_performance"],
        },
    )

@router.get("/data")
def review_data(
    target_date: date | None = None,
    db: Session = Depends(get_db),
):
    selected_date = target_date or today()

    return build_daily_review(
        db=db,
        target_date=selected_date,
    )