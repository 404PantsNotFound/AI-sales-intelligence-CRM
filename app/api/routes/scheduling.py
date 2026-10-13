from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.dashboard import (
    DashboardActivityListResponse,
    DashboardActivityRange,
)
from app.schemas.scheduling import (
    ActivityMoveRequest,
    ActivityMoveResponse,
    ScheduleAvailabilityRequest,
    ScheduleAvailabilityResponse,
)
from app.services.dashboard_service import get_workspace_activities
from app.services import calendar_sync_service
from app.services.activity_move_service import move_activity_to_date
from app.services.scheduling_service import (
    ScheduleRequest,
    default_duration,
    inspect_availability,
)

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


@router.get(
    "/activities",
    response_model=DashboardActivityListResponse,
    summary="List scheduled activities across the shared CRM workspace",
)
def list_workspace_activities(
    start_at: datetime = Query(),
    end_at: datetime = Query(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardActivityListResponse:
    try:
        activity_range = DashboardActivityRange(start_at=start_at, end_at=end_at)
    except ValidationError as exc:
        raise APIError(
            "The activity range must contain ordered timezone-aware timestamps.",
            422,
            "invalid_activity_range",
        ) from exc
    return get_workspace_activities(
        db, activity_range, organization_id=current_user.organization_id
    )


@router.post(
    "/availability",
    response_model=ScheduleAvailabilityResponse,
    summary="Check shared CRM scheduling availability",
)
def check_availability(
    request: ScheduleAvailabilityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleAvailabilityResponse:
    result = inspect_availability(
        db,
        ScheduleRequest(
            activity_type=request.activity_type,
            starts_at=request.starts_at,
            duration_minutes=request.duration_minutes
            or default_duration(request.activity_type),
            timezone_name=request.timezone_name,
            organization_id=current_user.organization_id,
            exclude_id=request.exclude_id,
        ),
    )
    return ScheduleAvailabilityResponse.model_validate(result)


@router.post(
    "/activities/{activity_type}/{record_id}/move",
    response_model=ActivityMoveResponse,
    summary="Move a scheduled activity to another local calendar date",
)
def move_workspace_activity(
    request: ActivityMoveRequest,
    activity_type: Literal["meeting", "call", "follow_up"],
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ActivityMoveResponse:
    if record_id < 1:
        raise APIError("Activity id must be positive.", 422, "invalid_activity_id")
    response = move_activity_to_date(
        db,
        activity_type,
        record_id,
        request.target_date,
        request.target_timezone,
        organization_id=current_user.organization_id,
    )
    calendar_sync_service.sync_activity(
        db, "followup" if activity_type == "follow_up" else activity_type, record_id
    )
    return response
