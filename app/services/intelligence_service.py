from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Call, Company, Customer, FollowUp, Meeting, SalesEnquiry
from app.services.analytics_service import _database_errors


@_database_errors
def get_stale_opportunities(
    db: Session,
    stale_days: int = 30,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_cutoff = now.timestamp() - (stale_days * 24 * 60 * 60)

    enquiries = db.execute(
        select(
            SalesEnquiry,
            Customer,
            Company,
        )
        .join(Customer, SalesEnquiry.customer_id == Customer.customer_id)
        .join(Company, Customer.company_id == Company.company_id)
        .where(SalesEnquiry.status.in_(("open", "in_progress")))
    ).all()

    meeting_rows = db.execute(
        select(
            Meeting.enquiry_id,
            Meeting.scheduled_at,
        )
        .where(Meeting.enquiry_id.is_not(None))
    ).all()

    call_rows = db.execute(
        select(
            Call.enquiry_id,
            Call.actual_time,
            Call.scheduled_at,
            Call.created_at,
        )
        .where(Call.enquiry_id.is_not(None))
    ).all()

    followup_rows = db.execute(
        select(
            FollowUp.enquiry_id,
            FollowUp.completed_at,
            FollowUp.created_at,
        )
        .where(FollowUp.enquiry_id.is_not(None))
    ).all()

    latest_activity: dict[int, datetime] = {}

    for enquiry_id, scheduled_at in meeting_rows:
        if scheduled_at is None:
            continue

        activity_time = (
            scheduled_at
            if scheduled_at.tzinfo is not None
            else scheduled_at.replace(tzinfo=timezone.utc)
        )

        if activity_time > now:
            continue

        current = latest_activity.get(enquiry_id)
        if current is None or activity_time > current:
            latest_activity[enquiry_id] = activity_time

    for enquiry_id, actual_time, scheduled_at, created_at in call_rows:
        activity_time = actual_time or scheduled_at or created_at
        if activity_time is None:
            continue

        activity_time = (
            activity_time
            if activity_time.tzinfo is not None
            else activity_time.replace(tzinfo=timezone.utc)
        )

        if activity_time > now:
            continue

        current = latest_activity.get(enquiry_id)
        if current is None or activity_time > current:
            latest_activity[enquiry_id] = activity_time

    for enquiry_id, completed_at, created_at in followup_rows:
        activity_time = completed_at or created_at
        if activity_time is None:
            continue

        activity_time = (
            activity_time
            if activity_time.tzinfo is not None
            else activity_time.replace(tzinfo=timezone.utc)
        )

        if activity_time > now:
            continue

        current = latest_activity.get(enquiry_id)
        if current is None or activity_time > current:
            latest_activity[enquiry_id] = activity_time

    stale_opportunities: list[dict[str, Any]] = []

    for enquiry, customer, company in enquiries:
        created_at = enquiry.created_at
        created_at = (
            created_at
            if created_at.tzinfo is not None
            else created_at.replace(tzinfo=timezone.utc)
        )

        activity_time = latest_activity.get(enquiry.enquiry_id, created_at)

        if activity_time.timestamp() >= stale_cutoff:
            continue

        stale_opportunities.append(
            {
                "enquiry_id": enquiry.enquiry_id,
                "customer_id": customer.customer_id,
                "customer_name": customer.customer_name,
                "company_id": company.company_id,
                "company_name": company.company_name,
                "product": enquiry.product,
                "enquiry_text": enquiry.enquiry_text,
                "priority": enquiry.priority,
                "status": enquiry.status,
                "estimated_value": enquiry.estimated_value,
                "created_at": created_at,
                "latest_activity_at": activity_time,
                "stale_days": (now - activity_time).days,
            }
        )

    stale_opportunities.sort(
        key=lambda opportunity: opportunity["latest_activity_at"]
    )

    return stale_opportunities