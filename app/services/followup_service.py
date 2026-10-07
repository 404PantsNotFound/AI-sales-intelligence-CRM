from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction, rollback_failed_transaction
from app.models import FollowUp
from app.schemas.follow_up import FollowUpCreate, FollowUpUpdate
from app.services.activity_validation import validate_customer_references

logger = logging.getLogger(__name__)


def create_followup(db: Session, data: FollowUpCreate) -> FollowUp:
    try:
        with atomic_transaction(db):
            validate_customer_references(
                db,
                data.customer_id,
                enquiry_id=data.enquiry_id,
                meeting_id=data.meeting_id,
                call_id=data.call_id,
            )
            followup = FollowUp(**data.model_dump())
            db.add(followup)
            db.flush()
            db.refresh(followup)
        return followup
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_followup", exc, conflict=True)
        raise APIError("Follow-up references conflict with CRM data.", 409, "followup_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_followup", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def get_followup(db: Session, followup_id: int) -> FollowUp:
    try:
        followup = db.scalar(
            select(FollowUp)
            .options(
                joinedload(FollowUp.enquiry),
                joinedload(FollowUp.meeting),
                joinedload(FollowUp.call),
            )
            .where(FollowUp.followup_id == followup_id)
        )
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_followup", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
    if followup is None:
        raise APIError("Follow-up not found.", 404, "followup_not_found")
    return followup


def update_followup(db: Session, followup_id: int, data: FollowUpUpdate) -> FollowUp:
    try:
        with atomic_transaction(db):
            followup = db.get(FollowUp, followup_id)
            if followup is None:
                raise APIError("Follow-up not found.", 404, "followup_not_found")
            updates = data.model_dump(exclude_unset=True)
            customer_id = updates.get("customer_id", followup.customer_id)
            validate_customer_references(
                db,
                customer_id,
                enquiry_id=updates.get("enquiry_id", followup.enquiry_id),
                meeting_id=updates.get("meeting_id", followup.meeting_id),
                call_id=updates.get("call_id", followup.call_id),
            )
            for field, value in updates.items():
                setattr(followup, field, value)
            db.flush()
            db.refresh(followup)
        return followup
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_followup", exc, conflict=True)
        raise APIError("Follow-up references conflict with CRM data.", 409, "followup_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_followup", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def complete_customer_followup(
    db: Session,
    customer_id: int,
    followup_id: int,
) -> FollowUp:
    try:
        with atomic_transaction(db):
            followup = db.scalar(
                select(FollowUp)
                .where(
                    FollowUp.followup_id == followup_id,
                    FollowUp.customer_id == customer_id,
                )
            )
            if followup is None:
                raise APIError(
                    "Follow-up not found for this customer.",
                    404,
                    "followup_not_found",
                )
            if followup.status == "completed":
                raise APIError(
                    "Follow-up is already completed.",
                    409,
                    "followup_already_completed",
                )
            validate_customer_references(
                db,
                customer_id,
                enquiry_id=followup.enquiry_id,
                meeting_id=followup.meeting_id,
                call_id=followup.call_id,
            )
            followup.status = "completed"
            followup.completed_at = datetime.now(timezone.utc)
            db.flush()
            db.refresh(followup)
        return followup
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "complete_customer_followup", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc


def list_customer_followups(db: Session, customer_id: int) -> list[FollowUp]:
    try:
        validate_customer_references(db, customer_id)
        return list(
            db.scalars(
                select(FollowUp)
                .options(
                    joinedload(FollowUp.enquiry),
                    joinedload(FollowUp.meeting),
                    joinedload(FollowUp.call),
                )
                .where(FollowUp.customer_id == customer_id)
                .order_by(FollowUp.due_date.desc(), FollowUp.followup_id.desc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "list_customer_followups", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
