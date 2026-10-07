from datetime import date, datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import rollback_failed_transaction
from app.models import Call, Customer, FollowUp, Meeting, SalesEnquiry
from app.schemas.activity import (
    ActivityResponse,
    ActivityTimelineResponse,
    ActivityType,
    CustomerOverviewResponse,
)
from app.schemas.call import CallResponse
from app.schemas.company import CompanyResponse
from app.schemas.contact import ContactResponse
from app.schemas.follow_up import FollowUpResponse
from app.schemas.meeting import MeetingResponse
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.services.activity_validation import require_customer

logger = logging.getLogger(__name__)


def _coerce_activity_date(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _within_date_range(
    activity_date: datetime,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    day = activity_date.date()
    return not (
        (start_date is not None and day < start_date)
        or (end_date is not None and day > end_date)
    )


def get_customer_activity(
    db: Session,
    customer_id: int,
    *,
    activity_type: ActivityType | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[ActivityResponse]:
    if start_date and end_date and start_date > end_date:
        raise APIError(
            "start_date must be on or before end_date.",
            status_code=422,
            code="invalid_date_range",
        )

    try:
        require_customer(db, customer_id)
        enquiries = db.scalars(
            select(SalesEnquiry).where(SalesEnquiry.customer_id == customer_id)
        ).all()
        meetings = db.scalars(
            select(Meeting)
            .options(joinedload(Meeting.contact))
            .where(Meeting.customer_id == customer_id)
        ).all()
        calls = db.scalars(
            select(Call)
            .options(joinedload(Call.contact))
            .where(Call.customer_id == customer_id)
        ).all()
        followups = db.scalars(
            select(FollowUp)
            .options(
                joinedload(FollowUp.enquiry),
                joinedload(FollowUp.meeting),
                joinedload(FollowUp.call),
            )
            .where(FollowUp.customer_id == customer_id)
        ).all()
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_customer_activity", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc

    activities: list[ActivityResponse] = []
    if activity_type in (None, "enquiry"):
        for enquiry in enquiries:
            activities.append(
                ActivityResponse(
                    activity_id=f"enquiry-{enquiry.enquiry_id}",
                    activity_type="enquiry",
                    activity_date=enquiry.created_at,
                    status=enquiry.status,
                    title=enquiry.product or "Sales enquiry",
                    description=enquiry.enquiry_text,
                )
            )
    if activity_type in (None, "meeting"):
        for meeting in meetings:
            description = meeting.summary or meeting.notes or meeting.agenda
            activities.append(
                ActivityResponse(
                    activity_id=f"meeting-{meeting.meeting_id}",
                    activity_type="meeting",
                    activity_date=meeting.scheduled_at,
                    status=meeting.status,
                    title=meeting.agenda or "Meeting",
                    description=description,
                )
            )
    if activity_type in (None, "call"):
        for call in calls:
            activities.append(
                ActivityResponse(
                    activity_id=f"call-{call.call_id}",
                    activity_type="call",
                    activity_date=call.actual_time or call.scheduled_at or call.created_at,
                    status=call.status,
                    title=call.call_type or "Sales call",
                    description=call.summary or call.outcome or call.notes,
                )
            )
    if activity_type in (None, "follow_up"):
        for followup in followups:
            activities.append(
                ActivityResponse(
                    activity_id=f"follow_up-{followup.followup_id}",
                    activity_type="follow_up",
                    activity_date=followup.due_date,
                    status=followup.status,
                    title=followup.type,
                    description=followup.description,
                )
            )

    filtered = [
        item
        for item in activities
        if _within_date_range(item.activity_date, start_date, end_date)
    ]
    return sorted(
        filtered,
        key=lambda item: _coerce_activity_date(item.activity_date),
        reverse=True,
    )


def get_customer_overview(db: Session, customer_id: int) -> CustomerOverviewResponse:
    try:
        customer = db.scalar(
            select(Customer)
            .options(
                joinedload(Customer.company),
                selectinload(Customer.contacts),
                selectinload(Customer.sales_enquiries),
                selectinload(Customer.meetings).joinedload(Meeting.contact),
                selectinload(Customer.meetings).joinedload(Meeting.enquiry),
                selectinload(Customer.calls).joinedload(Call.contact),
                selectinload(Customer.calls).joinedload(Call.enquiry),
                selectinload(Customer.followups).joinedload(FollowUp.enquiry),
                selectinload(Customer.followups).joinedload(FollowUp.meeting),
                selectinload(Customer.followups).joinedload(FollowUp.call),
            )
            .where(Customer.customer_id == customer_id)
        )
        if customer is None:
            raise APIError("Customer not found.", 404, "customer_not_found")
        activity = get_customer_activity(db, customer_id)
        return CustomerOverviewResponse(
            customer_id=customer.customer_id,
            customer_name=customer.customer_name,
            status=customer.status,
            sales_stage=customer.sales_stage,
            created_at=customer.created_at,
            updated_at=customer.updated_at,
            company=CompanyResponse.model_validate(customer.company),
            contacts=[ContactResponse.model_validate(item) for item in customer.contacts],
            sales_enquiries=[
                SalesEnquiryResponse.model_validate(item) for item in customer.sales_enquiries
            ],
            meetings=[MeetingResponse.model_validate(item) for item in customer.meetings],
            calls=[CallResponse.model_validate(item) for item in customer.calls],
            followups=[FollowUpResponse.model_validate(item) for item in customer.followups],
            activity=ActivityTimelineResponse(items=activity),
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_customer_overview", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
