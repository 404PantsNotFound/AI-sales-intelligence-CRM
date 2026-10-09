from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.models import Call, FollowUp, Meeting, SchedulingLock
from app.schemas.validators import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
    resolve_local_datetime,
    timezone_from_name,
    validate_timezone_name,
)

ActivityType = Literal["meeting", "call", "followup"]
_ACTIVE_FOLLOWUP_STATUSES = ("pending", "in_progress", "overdue")
_SCHEDULED_CALL_STATUSES = ("scheduled",)
_SCHEDULED_MEETING_STATUSES = ("scheduled",)
_MAX_ACTIVITY_DURATION_MINUTES = 1440
_UTC = timezone.utc


@dataclass(frozen=True)
class ScheduleSlot:
    activity_type: ActivityType
    record_id: int
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class ScheduleRequest:
    activity_type: ActivityType
    starts_at: datetime
    duration_minutes: int
    timezone_name: str
    exclude_id: int | None = None
    record_id: int | None = None


def default_duration(activity_type: ActivityType) -> int:
    return {
        "meeting": settings.scheduling_meeting_default_duration_minutes,
        "call": settings.scheduling_call_default_duration_minutes,
        "followup": settings.scheduling_followup_default_duration_minutes,
    }[activity_type]


def _workspace_schedule_lock_statement():
    return (
        select(SchedulingLock)
        .where(SchedulingLock.lock_id == 1)
        .with_for_update()
    )


def lock_workspace_schedule(db: Session) -> None:
    lock = db.scalar(_workspace_schedule_lock_statement())
    if lock is None:
        raise APIError(
            "Scheduling is unavailable until the scheduling lock migration is applied.",
            503,
            "scheduling_not_ready",
        )


def _normalize_request(request: ScheduleRequest) -> ScheduleRequest:
    if request.starts_at.tzinfo is None or request.starts_at.utcoffset() is None:
        raise APIError(
            "A timezone-aware activity start time is required.",
            422,
            "invalid_schedule",
        )
    if not 1 <= request.duration_minutes <= _MAX_ACTIVITY_DURATION_MINUTES:
        raise APIError(
            "Activity duration must be between 1 and 1440 minutes.",
            422,
            "invalid_schedule",
        )
    try:
        timezone_name = validate_timezone_name(request.timezone_name)
    except ValueError as exc:
        raise APIError("A valid scheduling timezone is required.", 422, "invalid_schedule") from exc
    return ScheduleRequest(
        activity_type=request.activity_type,
        starts_at=request.starts_at.astimezone(_UTC),
        duration_minutes=request.duration_minutes,
        timezone_name=timezone_name,
        exclude_id=request.exclude_id,
        record_id=request.record_id,
    )


def _busy_slots(
    db: Session,
    *,
    starts_before: datetime,
    starts_after: datetime,
    exclude: tuple[ActivityType, int] | None = None,
    lock_rows: bool = False,
) -> list[ScheduleSlot]:
    slots: list[ScheduleSlot] = []
    meeting_query = (
        select(Meeting)
        .where(
            Meeting.status.in_(_SCHEDULED_MEETING_STATUSES),
            Meeting.scheduled_at < starts_before,
            Meeting.scheduled_at >= starts_after,
        )
        .order_by(Meeting.meeting_id)
    )
    if lock_rows:
        meeting_query = meeting_query.with_for_update()
    rows = db.scalars(meeting_query).all()
    for row in rows:
        if exclude != ("meeting", row.meeting_id):
            duration = _stored_duration(row.duration, "meeting")
            slots.append(
                ScheduleSlot(
                    "meeting",
                    row.meeting_id,
                    _as_utc(row.scheduled_at),
                    _as_utc(row.scheduled_at) + timedelta(minutes=duration),
                )
            )

    call_query = (
        select(Call)
        .where(
            Call.status.in_(_SCHEDULED_CALL_STATUSES),
            Call.scheduled_at.is_not(None),
            Call.scheduled_at < starts_before,
            Call.scheduled_at >= starts_after,
        )
        .order_by(Call.call_id)
    )
    if lock_rows:
        call_query = call_query.with_for_update()
    rows = db.scalars(call_query).all()
    for row in rows:
        if exclude != ("call", row.call_id) and row.scheduled_at is not None:
            duration = _stored_duration(row.duration, "call")
            slots.append(
                ScheduleSlot(
                    "call",
                    row.call_id,
                    _as_utc(row.scheduled_at),
                    _as_utc(row.scheduled_at) + timedelta(minutes=duration),
                )
            )

    followup_query = (
        select(FollowUp)
        .where(
            FollowUp.status.in_(_ACTIVE_FOLLOWUP_STATUSES),
            FollowUp.due_date < starts_before,
            FollowUp.due_date >= starts_after,
        )
        .order_by(FollowUp.followup_id)
    )
    if lock_rows:
        followup_query = followup_query.with_for_update()
    rows = db.scalars(followup_query).all()
    for row in rows:
        if exclude != ("followup", row.followup_id):
            duration = _stored_duration(row.duration, "followup")
            slots.append(
                ScheduleSlot(
                    "followup",
                    row.followup_id,
                    _as_utc(row.due_date),
                    _as_utc(row.due_date) + timedelta(minutes=duration),
                )
            )
    return slots


def _stored_duration(value: int | None, activity_type: ActivityType) -> int:
    duration = default_duration(activity_type) if value is None else value
    if not 1 <= duration <= _MAX_ACTIVITY_DURATION_MINUTES:
        raise APIError(
            "A stored CRM activity has an invalid scheduling duration.",
            500,
            "invalid_schedule_data",
        )
    return duration


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


def _overlaps(start: datetime, end: datetime, slot: ScheduleSlot) -> bool:
    return start < slot.ends_at and slot.starts_at < end


def _suggestions(
    request: ScheduleRequest,
    busy: list[ScheduleSlot],
) -> list[dict[str, str | int]]:
    zone = timezone_from_name(request.timezone_name)
    now = datetime.now(_UTC)
    requested_local = request.starts_at.astimezone(zone)
    earliest = max(request.starts_at, now)
    horizon_end = requested_local.date() + timedelta(days=settings.scheduling_horizon_days)
    increment = settings.scheduling_increment_minutes
    opening = settings.scheduling_business_start_hour * 60
    closing = settings.scheduling_business_end_hour * 60
    suggestion_limit = min(5, settings.scheduling_suggestion_count)
    suggestions: list[dict[str, str | int]] = []
    seen: set[datetime] = set()

    for offset in range(settings.scheduling_horizon_days + 1):
        day = requested_local.date() + timedelta(days=offset)
        if day >= horizon_end or day.weekday() >= 5:
            continue
        minute = opening
        while minute + request.duration_minutes <= closing:
            try:
                local_start = resolve_local_datetime(
                    day,
                    time(hour=minute // 60, minute=minute % 60),
                    request.timezone_name,
                )
            except (AmbiguousLocalTimeError, NonexistentLocalTimeError):
                minute += increment
                continue
            start = local_start.astimezone(_UTC)
            end = start + timedelta(minutes=request.duration_minutes)
            local_end = end.astimezone(zone)
            end_minute = local_end.hour * 60 + local_end.minute
            within_business_hours = (
                local_end.date() == day and end_minute <= closing
            ) or (
                closing == 1440
                and local_end.date() == day + timedelta(days=1)
                and local_end.time() == time.min
            )
            if (
                start > request.starts_at
                and start >= earliest
                and start not in seen
                and within_business_hours
                and not any(_overlaps(start, end, slot) for slot in busy)
            ):
                seen.add(start)
                suggestions.append(
                    {
                        "starts_at": start.astimezone(zone).isoformat(),
                        "ends_at": end.astimezone(zone).isoformat(),
                        "timezone": request.timezone_name,
                        "duration_minutes": request.duration_minutes,
                    }
                )
                if len(suggestions) >= suggestion_limit:
                    return suggestions
            minute += increment
    return suggestions


def _search_end(request: ScheduleRequest) -> datetime:
    zone = timezone_from_name(request.timezone_name)
    end_date = request.starts_at.astimezone(zone).date() + timedelta(
        days=settings.scheduling_horizon_days
    )
    return datetime.combine(end_date, time.min, tzinfo=zone).astimezone(_UTC)


def inspect_availability(
    db: Session,
    request: ScheduleRequest,
    *,
    acquire_lock: bool = False,
    lock_rows: bool = False,
) -> dict[str, object]:
    request = _normalize_request(request)
    if acquire_lock:
        lock_workspace_schedule(db)
    start = request.starts_at
    end = start + timedelta(minutes=request.duration_minutes)
    busy = _busy_slots(
        db,
        starts_before=max(_search_end(request), end),
        starts_after=start - timedelta(minutes=_MAX_ACTIVITY_DURATION_MINUTES),
        exclude=(request.activity_type, request.exclude_id)
        if request.exclude_id is not None
        else None,
        lock_rows=lock_rows or acquire_lock,
    )
    conflicts = [slot for slot in busy if _overlaps(start, end, slot)]
    suggestions = _suggestions(request, busy) if conflicts else []
    return {
        "available": not conflicts,
        "activity_type": request.activity_type,
        "starts_at": start.astimezone(timezone_from_name(request.timezone_name)).isoformat(),
        "ends_at": end.astimezone(timezone_from_name(request.timezone_name)).isoformat(),
        "timezone": request.timezone_name,
        "duration_minutes": request.duration_minutes,
        "conflicts": [
            {
                "activity_type": slot.activity_type,
                "starts_at": slot.starts_at.astimezone(
                    timezone_from_name(request.timezone_name)
                ).isoformat(),
                "ends_at": slot.ends_at.astimezone(
                    timezone_from_name(request.timezone_name)
                ).isoformat(),
            }
            for slot in conflicts
        ],
        "suggestions": suggestions,
    }


def require_available(
    db: Session,
    request: ScheduleRequest,
    *,
    acquire_lock: bool = False,
    lock_rows: bool = True,
) -> ScheduleRequest:
    request = _normalize_request(request)
    result = inspect_availability(
        db,
        request,
        acquire_lock=acquire_lock,
        lock_rows=lock_rows,
    )
    if not result["available"]:
        raise APIError(
            "The requested time overlaps another scheduled CRM activity.",
            409,
            "schedule_conflict",
            details={
                key: value
                for key, value in result.items()
                if key in {"activity_type", "starts_at", "ends_at", "timezone",
                           "duration_minutes", "conflicts", "suggestions"}
            },
        )
    return request


def validate_schedule_batch(
    db: Session,
    requests: list[ScheduleRequest],
) -> None:
    lock_workspace_schedule(db)
    accepted: list[ScheduleSlot] = []
    for raw_request in requests:
        request = _normalize_request(raw_request)
        start = request.starts_at
        end = start + timedelta(minutes=request.duration_minutes)
        existing = _busy_slots(
            db,
            starts_before=max(_search_end(request), end),
            starts_after=start - timedelta(minutes=_MAX_ACTIVITY_DURATION_MINUTES),
            lock_rows=True,
        )
        conflicts = [slot for slot in [*existing, *accepted] if _overlaps(start, end, slot)]
        if conflicts:
            raise APIError(
                "The workbook contains overlapping scheduled CRM activities.",
                409,
                "schedule_conflict",
                details={
                    "activity_type": request.activity_type,
                    "starts_at": start.astimezone(
                        timezone_from_name(request.timezone_name)
                    ).isoformat(),
                    "ends_at": end.astimezone(
                        timezone_from_name(request.timezone_name)
                    ).isoformat(),
                    "timezone": request.timezone_name,
                    "duration_minutes": request.duration_minutes,
                    "conflicts": [
                        {
                            "activity_type": slot.activity_type,
                            "starts_at": slot.starts_at.isoformat(),
                            "ends_at": slot.ends_at.isoformat(),
                        }
                        for slot in conflicts
                    ],
                    "suggestions": _suggestions(request, [*existing, *accepted]),
                },
            )
        accepted.append(
            ScheduleSlot(
                request.activity_type,
                request.record_id or 0,
                start,
                end,
            )
        )
