from datetime import datetime
from decimal import Decimal

from sqlalchemy import inspect
from sqlalchemy.orm import configure_mappers

from app.database.connection import Base
from app.models import Call, Company, Contact, Customer, FollowUp, Meeting, SalesEnquiry, User
from app.schemas import (
    CallCreate,
    CallResponse,
    CompanyCreate,
    CompanyResponse,
    ContactCreate,
    ContactResponse,
    CustomerCreate,
    CustomerResponse,
    FollowUpCreate,
    FollowUpResponse,
    MeetingCreate,
    MeetingResponse,
    SalesEnquiryCreate,
    SalesEnquiryResponse,
)


def test_all_models_register_expected_tables() -> None:
    expected_columns = {
        "users": {
            "user_id", "email", "full_name", "password_hash", "role",
            "is_active", "created_at", "updated_at",
        },
        "companies": {
            "company_id", "company_name", "industry", "website", "address", "city",
            "country", "company_size", "description", "created_at", "updated_at",
        },
        "customers": {
            "customer_id", "company_id", "customer_name", "status", "sales_stage",
            "created_at", "updated_at",
        },
        "contacts": {
            "contact_id", "customer_id", "name", "job_title", "email", "phone",
            "is_primary", "created_at", "updated_at",
        },
        "sales_enquiries": {
            "enquiry_id", "customer_id", "product", "enquiry_text", "priority", "status",
            "estimated_value", "created_at", "updated_at",
        },
        "meetings": {
            "meeting_id", "customer_id", "contact_id", "enquiry_id", "scheduled_at",
            "duration", "status", "agenda", "notes", "summary", "created_at", "updated_at",
        },
        "calls": {
            "call_id", "customer_id", "contact_id", "enquiry_id", "call_type", "scheduled_at",
            "actual_time", "status", "outcome", "notes", "summary", "next_followup_date",
            "created_at", "updated_at",
        },
        "follow_ups": {
            "followup_id", "customer_id", "enquiry_id", "meeting_id", "call_id", "type",
            "due_date", "status", "description", "assigned_to", "completed_at", "created_at",
            "updated_at",
        },
    }

    assert set(Base.metadata.tables) == set(expected_columns)
    for table_name, columns in expected_columns.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == columns
    assert all(model.__table__ in Base.metadata.sorted_tables for model in (
        User, Company, Customer, Contact, SalesEnquiry, Meeting, Call, FollowUp
    ))


def test_foreign_keys_and_history_preserving_delete_rules() -> None:
    expected_foreign_keys = {
        "customers": {"company_id"},
        "contacts": {"customer_id"},
        "sales_enquiries": {"customer_id"},
        "meetings": {"customer_id", "contact_id", "enquiry_id"},
        "calls": {"customer_id", "contact_id", "enquiry_id"},
        "follow_ups": {"customer_id", "enquiry_id", "meeting_id", "call_id"},
    }
    for table_name, columns in expected_foreign_keys.items():
        table = Base.metadata.tables[table_name]
        assert columns <= {foreign_key.parent.name for foreign_key in table.foreign_keys}

    customer_company_fk = next(iter(Base.metadata.tables["customers"].c.company_id.foreign_keys))
    assert customer_company_fk.ondelete == "RESTRICT"
    for table_name in ("contacts", "sales_enquiries", "meetings", "calls", "follow_ups"):
        customer_fk = next(
            foreign_key
            for foreign_key in Base.metadata.tables[table_name].foreign_keys
            if foreign_key.parent.name == "customer_id"
        )
        assert customer_fk.ondelete == "RESTRICT"
    for table_name in ("meetings", "calls", "follow_ups"):
        for foreign_key in Base.metadata.tables[table_name].foreign_keys:
            if foreign_key.parent.name != "customer_id":
                assert foreign_key.ondelete == "SET NULL"


def test_relationships_are_configured_without_delete_cascade() -> None:
    configure_mappers()
    customer_relationships = inspect(Customer).relationships

    assert customer_relationships["company"].mapper.class_ is Company
    assert customer_relationships["contacts"].mapper.class_ is Contact
    assert customer_relationships["sales_enquiries"].mapper.class_ is SalesEnquiry
    assert customer_relationships["meetings"].mapper.class_ is Meeting
    assert customer_relationships["calls"].mapper.class_ is Call
    assert customer_relationships["followups"].mapper.class_ is FollowUp
    assert not customer_relationships["contacts"].cascade.delete
    assert not inspect(Company).relationships["customers"].cascade.delete
    assert inspect(Meeting).relationships["enquiry"].direction.name == "MANYTOONE"
    assert inspect(Call).relationships["contact"].direction.name == "MANYTOONE"


def test_create_schemas_validate_representative_data() -> None:
    now = datetime(2026, 10, 6, 10, 0, 0)
    assert CompanyCreate(company_name="Northwind").company_name == "Northwind"
    assert CustomerCreate(company_id=1, customer_name="Customer").company_id == 1
    assert ContactCreate(customer_id=1, name="A. Contact").is_primary is False
    enquiry = SalesEnquiryCreate(
        customer_id=1,
        enquiry_text="Interested in an annual plan",
        estimated_value=Decimal("1250.50"),
    )
    assert enquiry.estimated_value == Decimal("1250.50")
    assert MeetingCreate(customer_id=1, scheduled_at=now).enquiry_id is None
    assert CallCreate(customer_id=1, actual_time=now).status == "scheduled"
    followup = FollowUpCreate(customer_id=1, type="email", due_date=now)
    assert followup.meeting_id is None


def test_response_schemas_read_sqlalchemy_model_attributes() -> None:
    now = datetime(2026, 10, 6, 10, 0, 0)
    company = Company(company_id=1, company_name="Northwind", created_at=now, updated_at=now)
    customer = Customer(
        customer_id=1,
        company_id=1,
        customer_name="Customer",
        status="active",
        sales_stage="new",
        created_at=now,
        updated_at=now,
    )
    contact = Contact(
        contact_id=1,
        customer_id=1,
        name="A. Contact",
        is_primary=True,
        created_at=now,
        updated_at=now,
    )
    enquiry = SalesEnquiry(
        enquiry_id=1,
        customer_id=1,
        enquiry_text="Interested in an annual plan",
        priority="normal",
        status="open",
        estimated_value=Decimal("1250.50"),
        created_at=now,
        updated_at=now,
    )
    meeting = Meeting(
        meeting_id=1,
        customer_id=1,
        scheduled_at=now,
        status="scheduled",
        created_at=now,
        updated_at=now,
    )
    call = Call(
        call_id=1,
        customer_id=1,
        status="scheduled",
        created_at=now,
        updated_at=now,
    )
    followup = FollowUp(
        followup_id=1,
        customer_id=1,
        type="email",
        due_date=now,
        status="pending",
        created_at=now,
        updated_at=now,
    )

    responses = (
        CompanyResponse.model_validate(company),
        CustomerResponse.model_validate(customer),
        ContactResponse.model_validate(contact),
        SalesEnquiryResponse.model_validate(enquiry),
        MeetingResponse.model_validate(meeting),
        CallResponse.model_validate(call),
        FollowUpResponse.model_validate(followup),
    )
    assert len(responses) == 7
