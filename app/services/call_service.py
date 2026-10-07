import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction, rollback_failed_transaction
from app.models import Call
from app.schemas.call import CallCreate, CallUpdate
from app.services.activity_validation import validate_customer_references

logger = logging.getLogger(__name__)


def create_call(db: Session, data: CallCreate) -> Call:
    try:
        with atomic_transaction(db):
            validate_customer_references(
                db,
                data.customer_id,
                contact_id=data.contact_id,
                enquiry_id=data.enquiry_id,
            )
            call = Call(**data.model_dump())
            db.add(call)
            db.flush()
            db.refresh(call)
        return call
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_call", exc, conflict=True)
        raise APIError("Call references conflict with CRM data.", 409, "call_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "create_call", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def get_call(db: Session, call_id: int) -> Call:
    try:
        call = db.scalar(
            select(Call)
            .options(joinedload(Call.contact), joinedload(Call.enquiry))
            .where(Call.call_id == call_id)
        )
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_call", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
    if call is None:
        raise APIError("Call not found.", 404, "call_not_found")
    return call


def update_call(db: Session, call_id: int, data: CallUpdate) -> Call:
    try:
        with atomic_transaction(db):
            call = db.get(Call, call_id)
            if call is None:
                raise APIError("Call not found.", 404, "call_not_found")
            updates = data.model_dump(exclude_unset=True)
            customer_id = updates.get("customer_id", call.customer_id)
            validate_customer_references(
                db,
                customer_id,
                contact_id=updates.get("contact_id", call.contact_id),
                enquiry_id=updates.get("enquiry_id", call.enquiry_id),
            )
            for field, value in updates.items():
                setattr(call, field, value)
            db.flush()
            db.refresh(call)
        return call
    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_call", exc, conflict=True)
        raise APIError("Call references conflict with CRM data.", 409, "call_conflict") from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "update_call", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc


def list_customer_calls(db: Session, customer_id: int) -> list[Call]:
    try:
        validate_customer_references(db, customer_id)
        return list(
            db.scalars(
                select(Call)
                .options(joinedload(Call.contact), joinedload(Call.enquiry))
                .where(Call.customer_id == customer_id)
                .order_by(Call.actual_time.desc(), Call.scheduled_at.desc(), Call.call_id.desc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "list_customer_calls", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
