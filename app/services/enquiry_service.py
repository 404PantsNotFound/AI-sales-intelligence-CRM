import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction
from app.models import SalesEnquiry
from app.schemas.sales_enquiry import SalesEnquiryCreate, SalesEnquiryUpdate
from app.services.activity_validation import require_customer

logger = logging.getLogger(__name__)


def create_enquiry(db: Session, data: SalesEnquiryCreate) -> SalesEnquiry:
    try:
        with atomic_transaction(db):
            require_customer(db, data.customer_id)
            enquiry = SalesEnquiry(**data.model_dump())
            db.add(enquiry)
            db.flush()
            db.refresh(enquiry)
        return enquiry
    except APIError:
        raise
    except IntegrityError as exc:
        log_database_exception(logger, "create_enquiry", exc, conflict=True)
        raise APIError(
            "Sales enquiry references conflict with CRM data.",
            409,
            "enquiry_conflict",
        ) from exc
    except SQLAlchemyError as exc:
        log_database_exception(logger, "create_enquiry", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc


def get_enquiry(db: Session, enquiry_id: int) -> SalesEnquiry:
    try:
        enquiry = db.get(SalesEnquiry, enquiry_id)
    except SQLAlchemyError as exc:
        log_database_exception(logger, "get_enquiry", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc
    if enquiry is None:
        raise APIError("Sales enquiry not found.", 404, "enquiry_not_found")
    return enquiry


def update_enquiry(db: Session, enquiry_id: int, data: SalesEnquiryUpdate) -> SalesEnquiry:
    try:
        with atomic_transaction(db):
            enquiry = db.get(SalesEnquiry, enquiry_id)
            if enquiry is None:
                raise APIError("Sales enquiry not found.", 404, "enquiry_not_found")
            updates = data.model_dump(exclude_unset=True)
            target_customer_id = updates.get("customer_id", enquiry.customer_id)
            require_customer(db, target_customer_id)
            for field, value in updates.items():
                setattr(enquiry, field, value)
            db.flush()
            db.refresh(enquiry)
        return enquiry
    except APIError:
        raise
    except IntegrityError as exc:
        log_database_exception(logger, "update_enquiry", exc, conflict=True)
        raise APIError(
            "Sales enquiry references conflict with CRM data.",
            409,
            "enquiry_conflict",
        ) from exc
    except SQLAlchemyError as exc:
        log_database_exception(logger, "update_enquiry", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc


def list_customer_enquiries(db: Session, customer_id: int) -> list[SalesEnquiry]:
    try:
        require_customer(db, customer_id)
        return list(
            db.scalars(
                select(SalesEnquiry)
                .where(SalesEnquiry.customer_id == customer_id)
                .order_by(SalesEnquiry.created_at.desc(), SalesEnquiry.enquiry_id.desc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        log_database_exception(logger, "list_customer_enquiries", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc
