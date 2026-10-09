from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.database.connection import atomic_transaction
from app.models import Call, FollowUp, Meeting
from app.schemas.call import CallUpdate
from app.schemas.follow_up import FollowUpUpdate
from app.schemas.meeting import MeetingUpdate
from app.schemas.scheduling import ActivityMoveResponse
from app.schemas.validators import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
    resolve_local_datetime,
    timezone_from_name,
)
from app.services.call_service import get_call, update_call
from app.services.followup_service import get_followup, update_followup
from app.services.meeting_service import get_meeting, update_meeting
from app.services.scheduling_service import lock_workspace_schedule

ActivityKind = Literal["meeting", "call", "follow_up"]
_UTC = timezone.utc


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


def move_activity_to_date(
    db: Session,
    activity_type: ActivityKind,
    record_id: int,
    target_date: date,
    target_timezone: str,
) -> ActivityMoveResponse:
    with atomic_transaction(db):
        lock_workspace_schedule(db)
        if activity_type == "meeting":
            record: Meeting | Call | FollowUp = get_meeting(db, record_id)
            starts_at = record.scheduled_at
            timezone_name = record.scheduled_timezone or "UTC"
            active = record.status == "scheduled"
        elif activity_type == "call":
            record = get_call(db, record_id)
            starts_at = record.scheduled_at
            timezone_name = record.scheduled_timezone or "UTC"
            active = record.status == "scheduled"
        else:
            record = get_followup(db, record_id)
            starts_at = record.due_date
            timezone_name = record.due_timezone or "UTC"
            active = record.status in {"pending", "in_progress", "overdue"}

        if not active:
            raise APIError(
                "Only active scheduled activities can be moved.",
                409,
                "activity_not_movable",
            )
        if starts_at is None:
            raise APIError(
                "This call does not have a scheduled time to move.",
                409,
                "activity_not_movable",
            )

        try:
            timezone_from_name(timezone_name)
            target_zone = timezone_from_name(target_timezone)
        except ValueError as exc:
            raise APIError(
                "The activity has an invalid saved timezone and cannot be moved.",
                500,
                "invalid_schedule_data",
            ) from exc

        local_start = _aware_utc(starts_at).astimezone(target_zone)
        if local_start.date() == target_date:
            return ActivityMoveResponse(
                activity_id=f"{activity_type}-{record_id}",
                starts_at=_aware_utc(starts_at),
                timezone=timezone_name,
            )

        try:
            moved_start = resolve_local_datetime(
                target_date,
                local_start.timetz().replace(tzinfo=None),
                target_timezone,
                occurrence="later" if local_start.fold else "earlier",
            )
        except NonexistentLocalTimeError as exc:
            raise APIError(
                "The activity’s local time does not exist on the selected date because of a daylight-saving transition.",
                422,
                "invalid_move_time",
            ) from exc
        except AmbiguousLocalTimeError as exc:
            raise APIError(
                "The activity’s local time is ambiguous on the selected date.",
                422,
                "ambiguous_move_time",
            ) from exc

        if activity_type == "meeting":
            updated = update_meeting(
                db,
                record_id,
                MeetingUpdate(
                    scheduled_at=moved_start,
                    scheduled_timezone=timezone_name,
                ),
            )
            updated_start = updated.scheduled_at
        elif activity_type == "call":
            updated = update_call(
                db,
                record_id,
                CallUpdate(
                    scheduled_at=moved_start,
                    scheduled_timezone=timezone_name,
                ),
            )
            updated_start = updated.scheduled_at
        else:
            updated = update_followup(
                db,
                record_id,
                FollowUpUpdate(
                    due_date=moved_start,
                    due_timezone=timezone_name,
                ),
            )
            updated_start = updated.due_date

        response = ActivityMoveResponse(
            activity_id=f"{activity_type}-{record_id}",
            starts_at=_aware_utc(updated_start),
            timezone=timezone_name,
        )
    return response
