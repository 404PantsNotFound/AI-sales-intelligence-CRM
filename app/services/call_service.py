import logging

from app.core.config import settings
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction, rollback_failed_transaction
from app.models import Call
from app.schemas.call import CallCreate, CallUpdate
from app.services.activity_validation import owned, validate_customer_references
from app.services.scheduling_service import (
    ScheduleRequest,
    lock_workspace_schedule,
    require_available,
    require_future_start,
)
from app.schemas.validators import (
    normalize_utc_datetime,
    timezone_name_from_datetime,
)

logger = logging.getLogger(__name__)


def create_call(
    db: Session,
    data: CallCreate,
    *,
    organization_id: int,
    defer_commit: bool = False,
) -> Call:
    try:
        with atomic_transaction(db, commit_existing=not defer_commit):
            lock_workspace_schedule(db, organization_id)
            validate_customer_references(
                db,
                data.customer_id,
                organization_id=organization_id,
                contact_id=data.contact_id,
                enquiry_id=data.enquiry_id,
            )
            if data.status == "scheduled" and data.scheduled_at is not None:
                schedule = ScheduleRequest(
                    activity_type="call",
                    starts_at=data.scheduled_at,
                    duration_minutes=data.duration
                    or settings.scheduling_call_default_duration_minutes,
                    timezone_name=data.scheduled_timezone or "UTC",
                    organization_id=organization_id,
                )
                require_future_start(db, schedule)
                require_available(db, schedule)
            values = data.model_dump()
            if data.scheduled_at is not None:
                values["scheduled_at"] = normalize_utc_datetime(data.scheduled_at)
                values["duration"] = (
                    data.duration or settings.scheduling_call_default_duration_minutes
                )
            call = Call(**values, organization_id=organization_id)
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


def get_call(db: Session, call_id: int, *, organization_id: int) -> Call:
    try:
        call = db.scalar(
            select(Call)
            .options(joinedload(Call.contact), joinedload(Call.enquiry))
            .where(Call.call_id == call_id, Call.organization_id == organization_id)
        )
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_call", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
    if call is None:
        raise APIError("Call not found.", 404, "call_not_found")
    return call


def update_call(db: Session, call_id: int, data: CallUpdate, *, organization_id: int) -> Call:
    try:
        with atomic_transaction(db):
            lock_workspace_schedule(db, organization_id)
            call = owned(db, Call, call_id, organization_id=organization_id)
            if call is None:
                raise APIError("Call not found.", 404, "call_not_found")
            updates = data.model_dump(exclude_unset=True)
            if updates.get("scheduled_at") is not None:
                original_schedule = updates["scheduled_at"]
                if updates.get("scheduled_timezone") is None:
                    updates["scheduled_timezone"] = (
                        timezone_name_from_datetime(original_schedule)
                        if original_schedule.tzinfo is not None
                        and original_schedule.utcoffset() is not None
                        else "UTC"
                    )
                updates["scheduled_at"] = normalize_utc_datetime(original_schedule)
            customer_id = updates.get("customer_id", call.customer_id)
            target_status = updates.get("status", call.status)
            scheduled_at = normalize_utc_datetime(
                updates.get("scheduled_at", call.scheduled_at)
            )
            duration = updates.get("duration", call.duration)
            if duration is None:
                duration = settings.scheduling_call_default_duration_minutes
            if target_status == "scheduled" and scheduled_at is not None:
                schedule = ScheduleRequest(
                    activity_type="call",
                    starts_at=scheduled_at,
                    duration_minutes=duration,
                    timezone_name=updates.get(
                        "scheduled_timezone", call.scheduled_timezone or "UTC"
                    ),
                    organization_id=organization_id,
                    exclude_id=call.call_id,
                )
                # Untouched past calls stay editable; new or reopened times must be future.
                if (
                    call.status != "scheduled"
                    or scheduled_at != normalize_utc_datetime(call.scheduled_at)
                ):
                    require_future_start(db, schedule)
                require_available(db, schedule)
                updates["duration"] = duration
            validate_customer_references(
                db,
                customer_id,
                organization_id=organization_id,
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


def list_customer_calls(db: Session, customer_id: int, *, organization_id: int) -> list[Call]:
    try:
        validate_customer_references(db, customer_id, organization_id=organization_id)
        return list(
            db.scalars(
                select(Call)
                .options(joinedload(Call.contact), joinedload(Call.enquiry))
                .where(Call.customer_id == customer_id, Call.organization_id == organization_id)
                .order_by(Call.actual_time.desc(), Call.scheduled_at.desc(), Call.call_id.desc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "list_customer_calls", exc)
        raise APIError("The database operation could not be completed.", 500, "database_error") from exc
