import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction, rollback_failed_transaction
from app.models import Meeting
from app.schemas.meeting import MeetingCreate, MeetingUpdate
from app.services.activity_validation import validate_customer_references

logger = logging.getLogger(__name__)


def create_meeting(db: Session, data: MeetingCreate) -> Meeting:
    try:
        with atomic_transaction(db):
            validate_customer_references(
                db,
                data.customer_id,
                contact_id=data.contact_id,
                enquiry_id=data.enquiry_id,
            )
            meeting = Meeting(**data.model_dump())
            db.add(meeting)
            db.flush()
            db.refresh(meeting)
        return meeting
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_meeting", exc, conflict=True)
        raise APIError("Meeting references conflict with CRM data.", 409, "meeting_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_meeting", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def get_meeting(db: Session, meeting_id: int) -> Meeting:
    try:
        meeting = db.scalar(
            select(Meeting)
            .options(joinedload(Meeting.contact), joinedload(Meeting.enquiry))
            .where(Meeting.meeting_id == meeting_id)
        )
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_meeting", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
    if meeting is None:
        raise APIError("Meeting not found.", 404, "meeting_not_found")
    return meeting


def update_meeting(db: Session, meeting_id: int, data: MeetingUpdate) -> Meeting:
    try:
        with atomic_transaction(db):
            meeting = db.get(Meeting, meeting_id)
            if meeting is None:
                raise APIError("Meeting not found.", 404, "meeting_not_found")
            updates = data.model_dump(exclude_unset=True)
            customer_id = updates.get("customer_id", meeting.customer_id)
            validate_customer_references(
                db,
                customer_id,
                contact_id=updates.get("contact_id", meeting.contact_id),
                enquiry_id=updates.get("enquiry_id", meeting.enquiry_id),
            )
            for field, value in updates.items():
                setattr(meeting, field, value)
            db.flush()
            db.refresh(meeting)
        return meeting
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_meeting", exc, conflict=True)
        raise APIError("Meeting references conflict with CRM data.", 409, "meeting_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_meeting", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def list_customer_meetings(db: Session, customer_id: int) -> list[Meeting]:
    try:
        validate_customer_references(db, customer_id)
        return list(
            db.scalars(
                select(Meeting)
                .options(joinedload(Meeting.contact), joinedload(Meeting.enquiry))
                .where(Meeting.customer_id == customer_id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.meeting_id.desc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "list_customer_meetings", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
