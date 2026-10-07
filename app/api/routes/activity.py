from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.activity import (
    ActivityResponse,
    ActivityTimelineResponse,
    ActivityType,
    CustomerOverviewResponse,
)
from app.services import activity_service

router = APIRouter(prefix="/customers", tags=["customer activity"])


@router.get(
    "/{customer_id}/activity",
    response_model=ActivityTimelineResponse,
    summary="Get a customer's chronological activity timeline",
)
def get_customer_activity(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    activity_type: ActivityType | None = Query(default=None, alias="type"),
    start_date: date | None = None,
    end_date: date | None = None,
) -> ActivityTimelineResponse:
    items: list[ActivityResponse] = activity_service.get_customer_activity(
        db,
        customer_id,
        activity_type=activity_type,
        start_date=start_date,
        end_date=end_date,
    )
    return ActivityTimelineResponse(items=items)


@router.get(
    "/{customer_id}/overview",
    response_model=CustomerOverviewResponse,
    summary="Get a customer profile with CRM activity",
)
def get_customer_overview(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CustomerOverviewResponse:
    return activity_service.get_customer_overview(db, customer_id)

