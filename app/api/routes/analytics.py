from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.analytics import (
    ActivityMetrics,
    AnalyticsDateRange,
    CustomerMetrics,
    EnquiryMetrics,
    FollowupMetrics,
    OverviewMetrics,
    PipelineMetrics,
    TimeGrain,
)
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _date_range(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> AnalyticsDateRange:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date",
        )
    return AnalyticsDateRange(start_date=start_date, end_date=end_date)


DateRangeDependency = Annotated[AnalyticsDateRange, Depends(_date_range)]


@router.get("/overview", response_model=OverviewMetrics)
def get_overview(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
) -> OverviewMetrics:
    return analytics_service.get_overview_metrics(
        db, date_range.start_date, date_range.end_date
    )


@router.get("/customers", response_model=CustomerMetrics)
def get_customers(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
    grain: TimeGrain = Query(default="monthly"),
) -> CustomerMetrics:
    return analytics_service.get_customer_metrics(
        db, date_range.start_date, date_range.end_date, grain
    )


@router.get("/enquiries", response_model=EnquiryMetrics)
def get_enquiries(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
    grain: TimeGrain = Query(default="monthly"),
) -> EnquiryMetrics:
    return analytics_service.get_enquiry_metrics(
        db, date_range.start_date, date_range.end_date, grain
    )


@router.get("/activities", response_model=ActivityMetrics)
def get_activities(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
    grain: TimeGrain = Query(default="monthly"),
) -> ActivityMetrics:
    return analytics_service.get_activity_metrics(
        db, date_range.start_date, date_range.end_date, grain
    )


@router.get("/pipeline", response_model=PipelineMetrics)
def get_pipeline(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
) -> PipelineMetrics:
    return analytics_service.get_pipeline_metrics(
        db, date_range.start_date, date_range.end_date
    )


@router.get("/followups", response_model=FollowupMetrics)
def get_followups(
    date_range: DateRangeDependency,
    db: Session = Depends(get_db),
) -> FollowupMetrics:
    return analytics_service.get_followup_metrics(
        db, date_range.start_date, date_range.end_date
    )


@router.get("/test")
def test_analytics() -> dict[str, str]:
    return {"status": "ok", "module": "analytics"}
