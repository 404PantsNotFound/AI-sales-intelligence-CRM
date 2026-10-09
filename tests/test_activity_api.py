from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base, get_db
from app.main import app
from app.models import Call, Company, Contact, Customer, FollowUp, Meeting, SalesEnquiry
from tests.conftest import authorize_test_client


@pytest.fixture
def activity_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def activity_sessions(activity_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=activity_engine, expire_on_commit=False)


@pytest.fixture
def activity_client(
    activity_sessions: sessionmaker[Session],
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        with activity_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        authorize_test_client(activity_sessions, client)
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def activity_records(activity_sessions: sessionmaker[Session]) -> dict[str, int]:
    created_at = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    with activity_sessions() as db:
        first_company = Company(company_name="First Company")
        second_company = Company(company_name="Second Company")
        empty_company = Company(company_name="Empty Company")
        db.add_all([first_company, second_company, empty_company])
        db.flush()
        first_customer = Customer(
            company=first_company,
            customer_name="First Customer",
            status="active",
            sales_stage="new",
        )
        second_customer = Customer(
            company=second_company,
            customer_name="Second Customer",
            status="active",
            sales_stage="new",
        )
        empty_customer = Customer(
            company=empty_company,
            customer_name="Empty Customer",
            status="active",
            sales_stage="new",
        )
        db.add_all([first_customer, second_customer, empty_customer])
        db.flush()
        first_contact = Contact(customer=first_customer, name="First Contact", is_primary=True)
        second_contact = Contact(customer=second_customer, name="Second Contact", is_primary=True)
        first_enquiry = SalesEnquiry(
            customer=first_customer,
            product="First Product",
            enquiry_text="First enquiry",
            priority="normal",
            status="open",
            created_at=created_at,
        )
        second_enquiry = SalesEnquiry(
            customer=second_customer,
            product="Second Product",
            enquiry_text="Second enquiry",
            priority="normal",
            status="open",
            created_at=created_at,
        )
        db.add_all([first_contact, second_contact, first_enquiry, second_enquiry])
        db.flush()
        meeting = Meeting(
            customer=first_customer,
            scheduled_at=datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc),
            status="scheduled",
            agenda="Initial meeting",
        )
        call = Call(
            customer=first_customer,
            scheduled_at=datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc),
            status="scheduled",
            call_type="Discovery",
            notes="Call notes",
        )
        db.add_all([meeting, call])
        db.flush()
        followup = FollowUp(
            customer=first_customer,
            type="email",
            due_date=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
            status="pending",
            description="Send proposal",
        )
        db.add(followup)
        db.commit()
        return {
            "customer": first_customer.customer_id,
            "other_customer": second_customer.customer_id,
            "empty_customer": empty_customer.customer_id,
            "contact": first_contact.contact_id,
            "other_contact": second_contact.contact_id,
            "enquiry": first_enquiry.enquiry_id,
            "other_enquiry": second_enquiry.enquiry_id,
            "meeting": meeting.meeting_id,
            "call": call.call_id,
            "followup": followup.followup_id,
        }


def test_meeting_create_get_update_and_customer_list(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    created = activity_client.post(
        "/api/meetings",
        json={
            "customer_id": customer_id,
            "contact_id": activity_records["contact"],
            "enquiry_id": activity_records["enquiry"],
            "scheduled_at": "2026-10-07T10:30:00Z",
            "duration": 30,
            "agenda": "Discuss proposal",
        },
    )
    assert created.status_code == 201
    meeting_id = created.json()["meeting_id"]
    assert activity_client.get(f"/api/meetings/{meeting_id}").status_code == 200
    updated = activity_client.put(
        f"/api/meetings/{meeting_id}",
        json={"status": "completed", "notes": "Meeting held"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    customer_meetings = activity_client.get(f"/api/customers/{customer_id}/meetings")
    assert customer_meetings.status_code == 200
    assert len(customer_meetings.json()) == 2


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("customer_id", 9999, "customer_not_found"),
        ("contact_id", "other_contact", "contact_not_found"),
        ("enquiry_id", "other_enquiry", "enquiry_not_found"),
    ],
)
def test_meeting_rejects_missing_or_foreign_records(
    activity_client: TestClient,
    activity_records: dict[str, int],
    field: str,
    value: int | str,
    error_code: str,
) -> None:
    payload: dict[str, Any] = {
        "customer_id": activity_records["customer"],
        "scheduled_at": "2026-10-07T10:30:00Z",
    }
    payload[field] = activity_records[value] if isinstance(value, str) else value
    response = activity_client.post("/api/meetings", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == error_code


def test_meeting_duration_and_status_validation(activity_client: TestClient, activity_records: dict[str, int]) -> None:
    base = {
        "customer_id": activity_records["customer"],
        "scheduled_at": "2026-10-07T10:30:00Z",
    }
    assert activity_client.post("/api/meetings", json={**base, "duration": 0}).status_code == 422
    assert activity_client.post("/api/meetings", json={**base, "status": "tentative"}).status_code == 422


def test_call_create_get_update_and_customer_list(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    created = activity_client.post(
        "/api/calls",
        json={
            "customer_id": customer_id,
            "contact_id": activity_records["contact"],
            "enquiry_id": activity_records["enquiry"],
            "call_type": "Discovery",
            "scheduled_at": "2026-10-05T10:00:00Z",
            "status": "scheduled",
        },
    )
    assert created.status_code == 201
    call_id = created.json()["call_id"]
    assert activity_client.get(f"/api/calls/{call_id}").status_code == 200
    updated = activity_client.put(
        f"/api/calls/{call_id}",
        json={"status": "completed", "actual_time": "2026-10-05T10:15:00Z"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    listed = activity_client.get(f"/api/customers/{customer_id}/calls")
    assert listed.status_code == 200
    assert len(listed.json()) == 2


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("customer_id", 9999, "customer_not_found"),
        ("contact_id", "other_contact", "contact_not_found"),
        ("enquiry_id", "other_enquiry", "enquiry_not_found"),
    ],
)
def test_call_rejects_missing_or_foreign_records(
    activity_client: TestClient,
    activity_records: dict[str, int],
    field: str,
    value: int | str,
    error_code: str,
) -> None:
    payload: dict[str, Any] = {
        "customer_id": activity_records["customer"],
        "call_type": "Discovery",
    }
    payload[field] = activity_records[value] if isinstance(value, str) else value
    response = activity_client.post("/api/calls", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == error_code


def test_call_status_validation(activity_client: TestClient, activity_records: dict[str, int]) -> None:
    response = activity_client.post(
        "/api/calls",
        json={"customer_id": activity_records["customer"], "status": "ringing"},
    )
    assert response.status_code == 422


def test_followup_create_get_update_and_customer_list(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    independent = activity_client.post(
        "/api/followups",
        json={
            "customer_id": customer_id,
            "type": "task",
            "due_date": "2026-10-08T12:00:00Z",
            "description": "Independent follow-up",
        },
    )
    assert independent.status_code == 201
    linked = activity_client.post(
        "/api/followups",
        json={
            "customer_id": customer_id,
            "enquiry_id": activity_records["enquiry"],
            "meeting_id": activity_records["meeting"],
            "call_id": activity_records["call"],
            "type": "email",
            "due_date": "2026-10-09T12:00:00Z",
        },
    )
    assert linked.status_code == 201
    followup_id = linked.json()["followup_id"]
    assert activity_client.get(f"/api/followups/{followup_id}").status_code == 200
    updated = activity_client.put(
        f"/api/followups/{followup_id}",
        json={"status": "completed", "completed_at": "2026-10-09T13:00:00Z"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    listed = activity_client.get(f"/api/customers/{customer_id}/followups")
    assert listed.status_code == 200
    assert len(listed.json()) == 3


def test_followup_type_cannot_be_blank(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    response = activity_client.post(
        "/api/followups",
        json={
            "customer_id": activity_records["customer"],
            "type": "   ",
            "due_date": "2026-10-08T12:00:00Z",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("customer_id", 9999, "customer_not_found"),
        ("enquiry_id", "other_enquiry", "enquiry_not_found"),
        ("meeting_id", "other_meeting", "meeting_not_found"),
        ("call_id", "other_call", "call_not_found"),
    ],
)
def test_followup_rejects_missing_or_foreign_records(
    activity_client: TestClient,
    activity_records: dict[str, int],
    field: str,
    value: int | str,
    error_code: str,
) -> None:
    if value == "other_meeting":
        other_meeting = activity_client.post(
            "/api/meetings",
            json={
                "customer_id": activity_records["other_customer"],
                "scheduled_at": "2026-10-07T10:00:00Z",
            },
        ).json()["meeting_id"]
        related_id = other_meeting
    elif value == "other_call":
        other_call = activity_client.post(
            "/api/calls",
            json={"customer_id": activity_records["other_customer"], "call_type": "Other"},
        ).json()["call_id"]
        related_id = other_call
    else:
        related_id = activity_records[value] if isinstance(value, str) else value
    payload: dict[str, Any] = {
        "customer_id": activity_records["customer"],
        "type": "task",
        "due_date": "2026-10-08T12:00:00Z",
        field: related_id,
    }
    response = activity_client.post("/api/followups", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == error_code


def test_followup_update_validates_customer_ownership(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    created = activity_client.post(
        "/api/followups",
        json={
            "customer_id": activity_records["customer"],
            "enquiry_id": activity_records["enquiry"],
            "type": "email",
            "due_date": "2026-10-10T12:00:00Z",
        },
    )
    assert created.status_code == 201
    response = activity_client.put(
        f"/api/followups/{created.json()['followup_id']}",
        json={"customer_id": activity_records["other_customer"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "enquiry_not_found"


def test_activity_timeline_combines_sorts_and_filters_records(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    response = activity_client.get(f"/api/customers/{customer_id}/activity")
    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["activity_type"] for item in items} == {
        "enquiry",
        "meeting",
        "call",
        "follow_up",
    }
    dates = [datetime.fromisoformat(item["activity_date"].replace("Z", "+00:00")) for item in items]
    assert dates == sorted(dates, reverse=True)

    calls = activity_client.get(f"/api/customers/{customer_id}/activity?type=call")
    assert calls.status_code == 200
    assert {item["activity_type"] for item in calls.json()["items"]} == {"call"}
    october = activity_client.get(
        f"/api/customers/{customer_id}/activity",
        params={"start_date": "2026-10-03", "end_date": "2026-10-03"},
    )
    assert {item["activity_type"] for item in october.json()["items"]} == {"call"}


def test_activity_timeline_empty_date_validation_and_overview(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["empty_customer"]
    empty = activity_client.get(f"/api/customers/{customer_id}/activity")
    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    invalid_range = activity_client.get(
        f"/api/customers/{customer_id}/activity",
        params={"start_date": "2026-10-20", "end_date": "2026-10-01"},
    )
    assert invalid_range.status_code == 422
    invalid_date = activity_client.get(
        f"/api/customers/{customer_id}/activity",
        params={"start_date": "not-a-date"},
    )
    assert invalid_date.status_code == 422

    overview = activity_client.get(f"/api/customers/{activity_records['customer']}/overview")
    assert overview.status_code == 200
    assert len(overview.json()["meetings"]) == 1
    assert len(overview.json()["calls"]) == 1
    assert len(overview.json()["followups"]) == 1
    assert len(overview.json()["activity"]["items"]) == 4


def test_customer_overview_serializes_imported_medium_priority_and_missed_call(
    activity_client: TestClient,
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    with activity_sessions() as db:
        enquiry = db.get(SalesEnquiry, activity_records["enquiry"])
        call = db.get(Call, activity_records["call"])
        assert enquiry is not None
        assert call is not None
        enquiry.priority = "medium"
        call.status = "missed"
        db.commit()

    response = activity_client.get(
        f"/api/customers/{activity_records['customer']}/overview"
    )

    assert response.status_code == 200
    result = response.json()
    assert result["sales_enquiries"][0]["priority"] == "medium"
    assert result["calls"][0]["status"] == "missed"


def test_customer_overview_returns_explicit_error_for_invalid_stored_priority(
    activity_client: TestClient,
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    with activity_sessions() as db:
        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.execute(
            update(SalesEnquiry)
            .where(SalesEnquiry.enquiry_id == activity_records["enquiry"])
            .values(priority="critical")
        )
        db.commit()

    response = activity_client.get(
        f"/api/customers/{activity_records['customer']}/overview"
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "data_integrity_error"
    assert "do not match the API schema" in response.json()["error"]["message"]


def test_customer_activity_subresources_return_not_found(
    activity_client: TestClient,
) -> None:
    for path in ("meetings", "calls", "followups", "activity", "overview"):
        response = activity_client.get(f"/api/customers/500/{path}")
        assert response.status_code == 404
