from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import atomic_transaction, rollback_failed_transaction
from app.models import (
    Call,
    Company,
    Contact,
    Customer,
    FollowUp,
    Meeting,
    SalesEnquiry,
)

import logging

logger = logging.getLogger(__name__)


EXPECTED_SHEETS = {
    "Companies",
    "Customers",
    "Contacts",
    "Sales_Enquiries",
    "Meetings",
    "Calls",
    "Follow_Ups",
    "TEST_EXPECTATIONS",
}


def _rows(workbook: Any, sheet_name: str) -> list[dict[str, Any]]:
    worksheet = workbook[sheet_name]
    rows = list(worksheet.iter_rows(values_only=True))

    if not rows:
        return []

    headers = [
        str(value).strip() if value is not None else ""
        for value in rows[0]
    ]

    return [
        {
            headers[index]: value
            for index, value in enumerate(row)
            if index < len(headers) and headers[index]
        }
        for row in rows[1:]
        if any(value is not None for value in row)
    ]


def _require_columns(
    rows: list[dict[str, Any]],
    sheet_name: str,
    required: set[str],
) -> None:
    if not rows:
        raise APIError(
            f"The {sheet_name} sheet is empty.",
            status_code=422,
            code="invalid_import",
        )

    actual = set(rows[0].keys())
    missing = required - actual

    if missing:
        raise APIError(
            f"{sheet_name} is missing columns: "
            + ", ".join(sorted(missing)),
            status_code=422,
            code="invalid_import",
        )


def _datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise APIError(
                f"Invalid datetime value: {value}",
                status_code=422,
                code="invalid_import",
            ) from exc

    raise APIError(
        f"Invalid datetime value: {value!r}",
        status_code=422,
        code="invalid_import",
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None

    try:
        return Decimal(str(value))
    except Exception as exc:
        raise APIError(
            f"Invalid monetary value: {value!r}",
            status_code=422,
            code="invalid_import",
        ) from exc


def _optional(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    return None if value == "" else value


def import_crm_workbook(
    db: Session,
    file_bytes: bytes,
) -> dict[str, int]:
    try:
        workbook = load_workbook(
            filename=BytesIO(file_bytes),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        raise APIError(
            "The uploaded file is not a valid Excel workbook.",
            status_code=422,
            code="invalid_import",
        ) from exc

    actual_sheets = set(workbook.sheetnames)
    missing_sheets = EXPECTED_SHEETS - actual_sheets

    if missing_sheets:
        raise APIError(
            "Workbook is missing sheets: "
            + ", ".join(sorted(missing_sheets)),
            status_code=422,
            code="invalid_import",
        )

    companies = _rows(workbook, "Companies")
    customers = _rows(workbook, "Customers")
    contacts = _rows(workbook, "Contacts")
    enquiries = _rows(workbook, "Sales_Enquiries")
    meetings = _rows(workbook, "Meetings")
    calls = _rows(workbook, "Calls")
    followups = _rows(workbook, "Follow_Ups")

    _require_columns(
        companies,
        "Companies",
        {
            "company_id",
            "company_name",
            "industry",
            "website",
            "address",
            "city",
            "country",
            "company_size",
            "description",
        },
    )

    _require_columns(
        customers,
        "Customers",
        {
            "customer_id",
            "company_id",
            "customer_name",
            "status",
            "sales_stage",
        },
    )

    _require_columns(
        contacts,
        "Contacts",
        {
            "contact_id",
            "customer_id",
            "name",
            "job_title",
            "email",
            "phone",
            "is_primary",
        },
    )

    _require_columns(
        enquiries,
        "Sales_Enquiries",
        {
            "enquiry_id",
            "customer_id",
            "product",
            "enquiry_text",
            "priority",
            "status",
            "estimated_value",
            "created_at",
        },
    )

    _require_columns(
        meetings,
        "Meetings",
        {
            "meeting_id",
            "customer_id",
            "contact_id",
            "enquiry_id",
            "scheduled_at",
            "duration",
            "status",
            "agenda",
            "notes",
            "summary",
        },
    )

    _require_columns(
        calls,
        "Calls",
        {
            "call_id",
            "customer_id",
            "contact_id",
            "enquiry_id",
            "call_type",
            "scheduled_at",
            "actual_time",
            "status",
            "outcome",
            "notes",
            "summary",
            "next_followup_date",
        },
    )

    _require_columns(
        followups,
        "Follow_Ups",
        {
            "followup_id",
            "customer_id",
            "enquiry_id",
            "meeting_id",
            "call_id",
            "type",
            "due_date",
            "status",
            "description",
            "assigned_to",
            "completed_at",
        },
    )

    # ---------------------------------------------------------
    # Validate all relationships before inserting anything.
    # ---------------------------------------------------------

    company_ids = {row["company_id"] for row in companies}
    customer_ids = {row["customer_id"] for row in customers}
    contact_ids = {row["contact_id"] for row in contacts}
    enquiry_ids = {row["enquiry_id"] for row in enquiries}
    meeting_ids = {row["meeting_id"] for row in meetings}
    call_ids = {row["call_id"] for row in calls}

    for row in customers:
        if row["company_id"] not in company_ids:
            raise APIError(
                f"Customer {row['customer_id']} references "
                f"missing company {row['company_id']}.",
                status_code=422,
                code="invalid_import",
            )

    for row in contacts:
        if row["customer_id"] not in customer_ids:
            raise APIError(
                f"Contact {row['contact_id']} references "
                f"missing customer {row['customer_id']}.",
                status_code=422,
                code="invalid_import",
            )

    for row in enquiries:
        if row["customer_id"] not in customer_ids:
            raise APIError(
                f"Enquiry {row['enquiry_id']} references "
                f"missing customer {row['customer_id']}.",
                status_code=422,
                code="invalid_import",
            )

    for row in meetings:
        if row["customer_id"] not in customer_ids:
            raise APIError(
                f"Meeting {row['meeting_id']} references "
                f"missing customer {row['customer_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("contact_id") is not None
            and row["contact_id"] not in contact_ids
        ):
            raise APIError(
                f"Meeting {row['meeting_id']} references "
                f"missing contact {row['contact_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("enquiry_id") is not None
            and row["enquiry_id"] not in enquiry_ids
        ):
            raise APIError(
                f"Meeting {row['meeting_id']} references "
                f"missing enquiry {row['enquiry_id']}.",
                status_code=422,
                code="invalid_import",
            )

    for row in calls:
        if row["customer_id"] not in customer_ids:
            raise APIError(
                f"Call {row['call_id']} references "
                f"missing customer {row['customer_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("contact_id") is not None
            and row["contact_id"] not in contact_ids
        ):
            raise APIError(
                f"Call {row['call_id']} references "
                f"missing contact {row['contact_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("enquiry_id") is not None
            and row["enquiry_id"] not in enquiry_ids
        ):
            raise APIError(
                f"Call {row['call_id']} references "
                f"missing enquiry {row['enquiry_id']}.",
                status_code=422,
                code="invalid_import",
            )

    for row in followups:
        if row["customer_id"] not in customer_ids:
            raise APIError(
                f"Follow-up {row['followup_id']} references "
                f"missing customer {row['customer_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("enquiry_id") is not None
            and row["enquiry_id"] not in enquiry_ids
        ):
            raise APIError(
                f"Follow-up {row['followup_id']} references "
                f"missing enquiry {row['enquiry_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("meeting_id") is not None
            and row["meeting_id"] not in meeting_ids
        ):
            raise APIError(
                f"Follow-up {row['followup_id']} references "
                f"missing meeting {row['meeting_id']}.",
                status_code=422,
                code="invalid_import",
            )

        if (
            row.get("call_id") is not None
            and row["call_id"] not in call_ids
        ):
            raise APIError(
                f"Follow-up {row['followup_id']} references "
                f"missing call {row['call_id']}.",
                status_code=422,
                code="invalid_import",
            )

    # ---------------------------------------------------------
    # Transaction
    #
    # If ANY insert fails, the entire import is rolled back.
    # ---------------------------------------------------------

    try:
        with atomic_transaction(db):
            for row in companies:
                db.add(
                    Company(
                        company_id=row["company_id"],
                        company_name=row["company_name"],
                        industry=_optional(row, "industry"),
                        website=_optional(row, "website"),
                        address=_optional(row, "address"),
                        city=_optional(row, "city"),
                        country=_optional(row, "country"),
                        company_size=_optional(row, "company_size"),
                        description=_optional(row, "description"),
                    )
                )

            db.flush()

            for row in customers:
                db.add(
                    Customer(
                        customer_id=row["customer_id"],
                        company_id=row["company_id"],
                        customer_name=row["customer_name"],
                        status=row["status"],
                        sales_stage=row["sales_stage"],
                    )
                )

            db.flush()

            for row in contacts:
                db.add(
                    Contact(
                        contact_id=row["contact_id"],
                        customer_id=row["customer_id"],
                        name=row["name"],
                        job_title=_optional(row, "job_title"),
                        email=_optional(row, "email"),
                        phone=_optional(row, "phone"),
                        is_primary=bool(row.get("is_primary", False)),
                    )
                )

            db.flush()

            for row in enquiries:
                db.add(
                    SalesEnquiry(
                        enquiry_id=row["enquiry_id"],
                        customer_id=row["customer_id"],
                        product=_optional(row, "product"),
                        enquiry_text=row["enquiry_text"],
                        priority=row["priority"],
                        status=row["status"],
                        estimated_value=_decimal(
                            row.get("estimated_value")
                        ),
                        created_at=_datetime(row["created_at"]),
                    )
                )

            db.flush()

            for row in meetings:
                db.add(
                    Meeting(
                        meeting_id=row["meeting_id"],
                        customer_id=row["customer_id"],
                        contact_id=_optional(row, "contact_id"),
                        enquiry_id=_optional(row, "enquiry_id"),
                        scheduled_at=_datetime(row["scheduled_at"]),
                        duration=_optional(row, "duration"),
                        status=row["status"],
                        agenda=_optional(row, "agenda"),
                        notes=_optional(row, "notes"),
                        summary=_optional(row, "summary"),
                    )
                )

            db.flush()

            for row in calls:
                db.add(
                    Call(
                        call_id=row["call_id"],
                        customer_id=row["customer_id"],
                        contact_id=_optional(row, "contact_id"),
                        enquiry_id=_optional(row, "enquiry_id"),
                        call_type=_optional(row, "call_type"),
                        scheduled_at=_datetime(
                            row.get("scheduled_at")
                        ),
                        actual_time=_datetime(
                            row.get("actual_time")
                        ),
                        status=row["status"],
                        outcome=_optional(row, "outcome"),
                        notes=_optional(row, "notes"),
                        summary=_optional(row, "summary"),
                        next_followup_date=_datetime(
                            row.get("next_followup_date")
                        ),
                    )
                )

            db.flush()

            for row in followups:
                db.add(
                    FollowUp(
                        followup_id=row["followup_id"],
                        customer_id=row["customer_id"],
                        enquiry_id=_optional(row, "enquiry_id"),
                        meeting_id=_optional(row, "meeting_id"),
                        call_id=_optional(row, "call_id"),
                        type=row["type"],
                        due_date=_datetime(row["due_date"]),
                        status=row["status"],
                        description=_optional(row, "description"),
                        assigned_to=_optional(row, "assigned_to"),
                        completed_at=_datetime(
                            row.get("completed_at")
                        ),
                    )
                )

            db.flush()

    except APIError:
        raise
    except IntegrityError as exc:
        rollback_failed_transaction(db)
        log_database_exception(
            logger,
            "import_crm_workbook",
            exc,
            conflict=True,
        )
        raise APIError(
            "The CRM import conflicts with existing database data.",
            status_code=409,
            code="import_conflict",
        ) from exc
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(
            logger,
            "import_crm_workbook",
            exc,
        )
        raise APIError(
            "The CRM import could not be completed.",
            status_code=500,
            code="database_error",
        ) from exc

    return {
        "companies": len(companies),
        "customers": len(customers),
        "contacts": len(contacts),
        "sales_enquiries": len(enquiries),
        "meetings": len(meetings),
        "calls": len(calls),
        "follow_ups": len(followups),
    }