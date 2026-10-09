from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Call, Company, Customer, FollowUp, Meeting
from app.schemas.dashboard import (
    DashboardActivityListResponse,
    DashboardActivityRange,
    DashboardActivityResponse,
)
from app.schemas.validators import timezone_from_name
from app.services.scheduling_service import default_duration

_UTC = timezone.utc
_ACTIVE_FOLLOWUP_STATUSES = ("pending", "in_progress", "overdue")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


def _activity(
    *,
    activity_id: str,
    activity_type: Literal["meeting", "call", "follow_up"],
    customer: Customer,
    company_name: str,
    starts_at: datetime,
    timezone_name: str | None,
    duration: int | None,
    status: str,
    title: str,
    description: str | None,
) -> DashboardActivityResponse:
    zone_name = timezone_name or "UTC"
    zone = timezone_from_name(zone_name)
    return DashboardActivityResponse(
        activity_id=activity_id,
        activity_type=activity_type,
        customer_id=customer.customer_id,
        customer_name=customer.customer_name,
        company_name=company_name,
        starts_at=_aware_utc(starts_at).astimezone(zone),
        timezone=zone_name,
        duration_minutes=(
            duration
            if duration is not None
            else default_duration(
                "followup" if activity_type == "follow_up" else activity_type
            )
        ),
        status=status,
        title=title,
        description=description,
    )


def get_workspace_activities(
    db: Session,
    activity_range: DashboardActivityRange,
) -> DashboardActivityListResponse:
    starts_after = activity_range.start_at.astimezone(_UTC)
    starts_before = activity_range.end_at.astimezone(_UTC)

    meeting_rows = db.execute(
        select(Meeting, Customer, Company)
        .join(Customer, Customer.customer_id == Meeting.customer_id)
        .join(Company, Company.company_id == Customer.company_id)
        .where(
            Meeting.scheduled_at >= starts_after,
            Meeting.scheduled_at < starts_before,
        )
        .order_by(Meeting.scheduled_at, Meeting.meeting_id)
    ).all()
    call_rows = db.execute(
        select(Call, Customer, Company)
        .join(Customer, Customer.customer_id == Call.customer_id)
        .join(Company, Company.company_id == Customer.company_id)
        .where(
            Call.scheduled_at.is_not(None),
            Call.scheduled_at >= starts_after,
            Call.scheduled_at < starts_before,
        )
        .order_by(Call.scheduled_at, Call.call_id)
    ).all()
    followup_rows = db.execute(
        select(FollowUp, Customer, Company)
        .join(Customer, Customer.customer_id == FollowUp.customer_id)
        .join(Company, Company.company_id == Customer.company_id)
        .where(
            or_(
                and_(
                    FollowUp.due_date >= starts_after,
                    FollowUp.due_date < starts_before,
                ),
                and_(
                    FollowUp.due_date < starts_after,
                    FollowUp.status.in_(_ACTIVE_FOLLOWUP_STATUSES),
                ),
            )
        )
        .order_by(FollowUp.due_date, FollowUp.followup_id)
    ).all()

    items: list[DashboardActivityResponse] = []
    for meeting, customer, company in meeting_rows:
        items.append(
            _activity(
                activity_id=f"meeting-{meeting.meeting_id}",
                activity_type="meeting",
                customer=customer,
                company_name=company.company_name,
                starts_at=meeting.scheduled_at,
                timezone_name=meeting.scheduled_timezone,
                duration=meeting.duration,
                status=meeting.status,
                title=meeting.agenda or "Meeting",
                description=meeting.summary or meeting.notes,
            )
        )
    for call, customer, company in call_rows:
        assert call.scheduled_at is not None
        items.append(
            _activity(
                activity_id=f"call-{call.call_id}",
                activity_type="call",
                customer=customer,
                company_name=company.company_name,
                starts_at=call.scheduled_at,
                timezone_name=call.scheduled_timezone,
                duration=call.duration,
                status=call.status,
                title=call.call_type or "Sales call",
                description=call.summary or call.outcome or call.notes,
            )
        )
    for followup, customer, company in followup_rows:
        items.append(
            _activity(
                activity_id=f"follow_up-{followup.followup_id}",
                activity_type="follow_up",
                customer=customer,
                company_name=company.company_name,
                starts_at=followup.due_date,
                timezone_name=followup.due_timezone,
                duration=followup.duration,
                status=followup.status,
                title=followup.type,
                description=followup.description,
            )
        )

    items.sort(key=lambda item: item.starts_at.astimezone(_UTC))
    return DashboardActivityListResponse(items=items)
