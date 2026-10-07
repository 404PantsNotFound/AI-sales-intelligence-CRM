from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.models import Call, Contact, Customer, FollowUp, Meeting, SalesEnquiry


def require_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise APIError("Customer not found.", status_code=404, code="customer_not_found")
    return customer


def validate_customer_references(
    db: Session,
    customer_id: int,
    *,
    contact_id: int | None = None,
    enquiry_id: int | None = None,
    meeting_id: int | None = None,
    call_id: int | None = None,
    followup_id: int | None = None,
) -> None:
    require_customer(db, customer_id)
    references = (
        (Contact, contact_id, "contact"),
        (SalesEnquiry, enquiry_id, "enquiry"),
        (Meeting, meeting_id, "meeting"),
        (Call, call_id, "call"),
        (FollowUp, followup_id, "followup"),
    )
    for model, record_id, label in references:
        if record_id is None:
            continue
        record = db.scalar(
            select(model).where(
                model.customer_id == customer_id,
                getattr(model, f"{label}_id") == record_id,
            )
        )
        if record is None:
            raise APIError(
                f"The selected {label} does not belong to this customer or does not exist.",
                status_code=404,
                code=f"{label}_not_found",
            )

