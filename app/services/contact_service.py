import logging

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction
from app.models import Contact
from app.schemas.contact import ContactCreate, ContactUpdate
from app.services.activity_validation import require_customer

logger = logging.getLogger(__name__)


def _clear_primary_contacts(
    db: Session,
    customer_id: int,
    *,
    exclude_contact_id: int | None = None,
) -> None:
    stmt = (
        update(Contact)
        .where(Contact.customer_id == customer_id, Contact.is_primary.is_(True))
        .values(is_primary=False)
    )
    if exclude_contact_id is not None:
        stmt = stmt.where(Contact.contact_id != exclude_contact_id)
    db.execute(stmt)


def create_contact(db: Session, data: ContactCreate) -> Contact:
    try:
        with atomic_transaction(db):
            require_customer(db, data.customer_id)
            if data.is_primary:
                _clear_primary_contacts(db, data.customer_id)
            payload = data.model_dump()
            if payload.get("email") is not None:
                payload["email"] = str(payload["email"])
            contact = Contact(**payload)
            db.add(contact)
            db.flush()
            db.refresh(contact)
        return contact
    except APIError:
        raise
    except IntegrityError as exc:
        log_database_exception(logger, "create_contact", exc, conflict=True)
        raise APIError(
            "Contact references conflict with CRM data.",
            409,
            "contact_conflict",
        ) from exc
    except SQLAlchemyError as exc:
        log_database_exception(logger, "create_contact", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc


def get_contact(db: Session, contact_id: int) -> Contact:
    try:
        contact = db.get(Contact, contact_id)
    except SQLAlchemyError as exc:
        log_database_exception(logger, "get_contact", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc
    if contact is None:
        raise APIError("Contact not found.", 404, "contact_not_found")
    return contact


def update_contact(db: Session, contact_id: int, data: ContactUpdate) -> Contact:
    try:
        with atomic_transaction(db):
            contact = db.get(Contact, contact_id)
            if contact is None:
                raise APIError("Contact not found.", 404, "contact_not_found")
            updates = data.model_dump(exclude_unset=True)
            target_customer_id = updates.get("customer_id", contact.customer_id)
            require_customer(db, target_customer_id)
            if updates.get("email") is not None:
                updates["email"] = str(updates["email"])
            if updates.get("is_primary") is True:
                _clear_primary_contacts(
                    db,
                    target_customer_id,
                    exclude_contact_id=contact.contact_id,
                )
            for field, value in updates.items():
                setattr(contact, field, value)
            db.flush()
            db.refresh(contact)
        return contact
    except APIError:
        raise
    except IntegrityError as exc:
        log_database_exception(logger, "update_contact", exc, conflict=True)
        raise APIError(
            "Contact references conflict with CRM data.",
            409,
            "contact_conflict",
        ) from exc
    except SQLAlchemyError as exc:
        log_database_exception(logger, "update_contact", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc


def list_customer_contacts(db: Session, customer_id: int) -> list[Contact]:
    try:
        require_customer(db, customer_id)
        return list(
            db.scalars(
                select(Contact)
                .where(Contact.customer_id == customer_id)
                .order_by(Contact.is_primary.desc(), Contact.contact_id.asc())
            ).all()
        )
    except APIError:
        raise
    except SQLAlchemyError as exc:
        log_database_exception(logger, "list_customer_contacts", exc)
        raise APIError(
            "The database operation could not be completed.",
            500,
            "database_error",
        ) from exc
