from collections.abc import Generator
from datetime import datetime, time, timedelta, timezone
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text, update
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base, get_db
from app.core.exceptions import APIError
from app.main import app
from app.models import (
    Call,
    Company,
    Contact,
    Customer,
    FollowUp,
    Meeting,
    SalesEnquiry,
    SchedulingLock,
)
from app.services.activity_validation import (
    validate_contact_reassignment,
    validate_customer_references,
    validate_enquiry_reassignment,
)
from app.services.scheduling_service import (
    ScheduleRequest,
    _workspace_schedule_lock_statement,
    inspect_availability,
    require_available,
)
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
    with Session(engine) as db:
        db.add(SchedulingLock(lock_id=1))
        db.commit()
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


def test_meeting_partial_update_preserves_saved_timezone_and_instant(
    activity_client: TestClient,
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    meeting_id = activity_records["meeting"]
    original_instant = datetime(2026, 10, 2, 10, 0)
    with activity_sessions() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        meeting.scheduled_timezone = "Asia/Dubai"
        db.commit()

    response = activity_client.put(
        f"/api/meetings/{meeting_id}",
        json={"agenda": "Updated agenda"},
    )

    assert response.status_code == 200
    assert response.json()["scheduled_at"] == "2026-10-02T14:00:00+04:00"
    assert response.json()["scheduled_timezone"] == "Asia/Dubai"
    with activity_sessions() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        assert meeting.scheduled_at == original_instant
        assert meeting.scheduled_timezone == "Asia/Dubai"


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


def test_call_and_followup_preserve_saved_timezone_through_api(
    activity_client: TestClient,
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    call_response = activity_client.post(
        "/api/calls",
        json={
            "customer_id": activity_records["customer"],
            "scheduled_at": "2030-01-10T09:00:00+04:00",
            "scheduled_timezone": "Asia/Dubai",
            "duration": 30,
            "status": "scheduled",
        },
    )
    assert call_response.status_code == 201
    assert call_response.json()["scheduled_at"] == "2030-01-10T09:00:00+04:00"
    assert call_response.json()["scheduled_timezone"] == "Asia/Dubai"

    followup_response = activity_client.post(
        "/api/followups",
        json={
            "customer_id": activity_records["customer"],
            "type": "email",
            "due_date": "2030-01-10T10:00:00+04:00",
            "due_timezone": "Asia/Dubai",
            "status": "pending",
        },
    )
    assert followup_response.status_code == 201
    assert followup_response.json()["due_date"] == "2030-01-10T10:00:00+04:00"
    assert followup_response.json()["due_timezone"] == "Asia/Dubai"
    timeline_response = activity_client.get(
        f"/api/customers/{activity_records['customer']}/activity"
    )
    assert timeline_response.status_code == 200
    timeline = {
        item["activity_id"]: item
        for item in timeline_response.json()["items"]
    }
    assert timeline[f"call-{call_response.json()['call_id']}"]["activity_timezone"] == "Asia/Dubai"
    assert timeline[f"follow_up-{followup_response.json()['followup_id']}"]["activity_timezone"] == "Asia/Dubai"
    with activity_sessions() as db:
        call = db.get(Call, call_response.json()["call_id"])
        followup = db.get(FollowUp, followup_response.json()["followup_id"])
        assert call is not None and call.scheduled_at == datetime(2030, 1, 10, 5, 0)
        assert followup is not None and followup.due_date == datetime(2030, 1, 10, 6, 0)


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


def test_contact_reassignment_is_rejected_when_meetings_or_calls_reference_it(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    contact_id = activity_records["contact"]
    assert activity_client.post(
        "/api/meetings",
        json={
            "customer_id": customer_id,
            "contact_id": contact_id,
            "scheduled_at": "2026-10-07T10:30:00Z",
        },
    ).status_code == 201
    assert activity_client.post(
        "/api/calls",
        json={"customer_id": customer_id, "contact_id": contact_id},
    ).status_code == 201

    response = activity_client.put(
        f"/api/contacts/{contact_id}",
        json={"customer_id": activity_records["other_customer"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "contact_has_activity"
    contact = activity_client.get(f"/api/contacts/{contact_id}")
    assert contact.status_code == 200
    assert contact.json()["customer_id"] == customer_id


def test_enquiry_reassignment_is_rejected_when_activities_reference_it(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    customer_id = activity_records["customer"]
    enquiry_id = activity_records["enquiry"]
    assert activity_client.post(
        "/api/meetings",
        json={
            "customer_id": customer_id,
            "enquiry_id": enquiry_id,
            "scheduled_at": "2026-10-07T10:30:00Z",
        },
    ).status_code == 201
    assert activity_client.post(
        "/api/calls",
        json={"customer_id": customer_id, "enquiry_id": enquiry_id},
    ).status_code == 201
    assert activity_client.post(
        "/api/followups",
        json={
            "customer_id": customer_id,
            "enquiry_id": enquiry_id,
            "type": "task",
            "due_date": "2026-10-08T12:00:00Z",
        },
    ).status_code == 201

    response = activity_client.put(
        f"/api/enquiries/{enquiry_id}",
        json={"customer_id": activity_records["other_customer"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "enquiry_has_activity"
    enquiry = activity_client.get(f"/api/enquiries/{enquiry_id}")
    assert enquiry.status_code == 200
    assert enquiry.json()["customer_id"] == customer_id


def test_contact_and_enquiry_can_be_reassigned_without_linked_activities(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    contact_response = activity_client.put(
        f"/api/contacts/{activity_records['contact']}",
        json={"customer_id": activity_records["other_customer"]},
    )
    enquiry_response = activity_client.put(
        f"/api/enquiries/{activity_records['enquiry']}",
        json={"customer_id": activity_records["other_customer"]},
    )

    assert contact_response.status_code == 200
    assert contact_response.json()["customer_id"] == activity_records["other_customer"]
    assert enquiry_response.status_code == 200
    assert enquiry_response.json()["customer_id"] == activity_records["other_customer"]


def test_mysql_relationship_checks_use_ordered_current_reads() -> None:
    contact_db = Mock()
    contact_db.scalar.side_effect = [None, None]
    validate_contact_reassignment(contact_db, 10)

    contact_sql = [
        str(call.args[0].compile(dialect=mysql_dialect())).upper()
        for call in contact_db.scalar.call_args_list
    ]
    assert ["FROM MEETINGS" in statement for statement in contact_sql] == [True, False]
    assert ["FROM CALLS" in statement for statement in contact_sql] == [False, True]
    assert all("FOR UPDATE" in statement for statement in contact_sql)

    enquiry_db = Mock()
    enquiry_db.scalar.side_effect = [None, None, None]
    validate_enquiry_reassignment(enquiry_db, 20)

    enquiry_sql = [
        str(call.args[0].compile(dialect=mysql_dialect())).upper()
        for call in enquiry_db.scalar.call_args_list
    ]
    assert ["FROM MEETINGS" in statement for statement in enquiry_sql] == [
        True,
        False,
        False,
    ]
    assert ["FROM CALLS" in statement for statement in enquiry_sql] == [
        False,
        True,
        False,
    ]
    assert ["FROM FOLLOW_UPS" in statement for statement in enquiry_sql] == [
        False,
        False,
        True,
    ]
    assert all("FOR UPDATE" in statement for statement in enquiry_sql)

    activity_db = Mock()
    activity_db.get.return_value = object()
    activity_db.scalar.side_effect = [object(), object()]
    validate_customer_references(
        activity_db,
        1,
        contact_id=10,
        enquiry_id=20,
    )
    activity_sql = [
        str(call.args[0].compile(dialect=mysql_dialect())).upper()
        for call in activity_db.scalar.call_args_list
    ]
    assert "FROM CONTACTS" in activity_sql[0]
    assert "FROM SALES_ENQUIRIES" in activity_sql[1]
    assert all("FOR UPDATE" in statement for statement in activity_sql)


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


def test_scheduling_uses_cross_activity_overlaps_and_half_open_slots(
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    next_day = datetime.now(timezone.utc).date() + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    starts_at = datetime.combine(next_day, time(10, 0), tzinfo=timezone.utc)
    with activity_sessions() as db:
        db.add(
            Meeting(
                customer_id=activity_records["other_customer"],
                scheduled_at=starts_at,
                status="scheduled",
            )
        )
        db.add(
            Call(
                customer_id=activity_records["customer"],
                scheduled_at=starts_at + timedelta(minutes=120),
                status="scheduled",
            )
        )
        db.add(
            FollowUp(
                customer_id=activity_records["customer"],
                type="email",
                due_date=starts_at + timedelta(minutes=170),
                status="in_progress",
            )
        )
        db.add(
            Meeting(
                customer_id=activity_records["customer"],
                scheduled_at=starts_at + timedelta(minutes=240),
                duration=60,
                status="cancelled",
            )
        )
        db.add(
            Call(
                customer_id=activity_records["customer"],
                scheduled_at=starts_at + timedelta(minutes=260),
                duration=30,
                status="cancelled",
            )
        )
        db.add(
            FollowUp(
                customer_id=activity_records["customer"],
                type="email",
                due_date=starts_at + timedelta(minutes=250),
                duration=15,
                status="completed",
            )
        )
        db.commit()

        meeting_conflict = inspect_availability(
            db,
            ScheduleRequest(
                "call",
                starts_at + timedelta(minutes=30),
                30,
                "UTC",
            ),
        )
        assert meeting_conflict["available"] is False
        assert meeting_conflict["conflicts"][0]["activity_type"] == "meeting"

        adjacent = inspect_availability(
            db,
            ScheduleRequest("call", starts_at - timedelta(minutes=30), 30, "UTC"),
        )
        assert adjacent["available"] is True

        call_conflict = inspect_availability(
            db,
            ScheduleRequest(
                "followup",
                starts_at + timedelta(minutes=125),
                15,
                "UTC",
            ),
        )
        assert call_conflict["available"] is False
        assert call_conflict["conflicts"][0]["activity_type"] == "call"

        followup_conflict = inspect_availability(
            db,
            ScheduleRequest(
                "meeting",
                starts_at + timedelta(minutes=175),
                30,
                "UTC",
            ),
        )
        assert followup_conflict["available"] is False
        assert followup_conflict["conflicts"][0]["activity_type"] == "followup"

        inactive_only = inspect_availability(
            db,
            ScheduleRequest(
                "meeting",
                starts_at + timedelta(minutes=245),
                15,
                "UTC",
            ),
        )
        assert inactive_only["available"] is True


def test_schedule_conflict_has_timezone_aware_suggestions(
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    next_day = datetime.now(timezone.utc).date() + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    starts_at = datetime.combine(next_day, time(10, 0), tzinfo=timezone.utc)
    with activity_sessions() as db:
        db.add(
            Meeting(
                customer_id=activity_records["customer"],
                scheduled_at=starts_at,
                duration=60,
                status="scheduled",
            )
        )
        db.commit()
        with pytest.raises(APIError) as exc_info:
            require_available(
                db,
                ScheduleRequest("meeting", starts_at, 60, "UTC"),
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "schedule_conflict"
    suggestions = exc_info.value.details["suggestions"]
    assert 1 <= len(suggestions) <= 5
    assert all(item["timezone"] == "UTC" for item in suggestions)
    suggestion_starts = [
        datetime.fromisoformat(item["starts_at"])
        for item in suggestions
    ]
    assert len(set(suggestion_starts)) == len(suggestion_starts)
    for item, suggestion_start in zip(suggestions, suggestion_starts):
        suggestion_end = datetime.fromisoformat(item["ends_at"])
        assert suggestion_start > starts_at
        assert suggestion_start.utcoffset() == timedelta(0)
        assert suggestion_end - suggestion_start == timedelta(minutes=60)
        assert suggestion_start.weekday() < 5
        assert time(9) <= suggestion_start.timetz().replace(tzinfo=None) < time(17)
        assert not (
            suggestion_start < starts_at + timedelta(minutes=60)
            and starts_at < suggestion_end
        )


def test_availability_api_returns_conflict_details(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    response = activity_client.post(
        "/api/scheduling/availability",
        json={
            "activity_type": "call",
            "starts_at": "2026-10-02T10:30:00Z",
            "duration_minutes": 30,
            "timezone_name": "UTC",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["conflicts"][0]["activity_type"] == "meeting"
    assert body["suggestions"]
    assert all("record_id" not in conflict for conflict in body["conflicts"])
    assert all(item["timezone"] == "UTC" for item in body["suggestions"])


def test_meeting_create_rechecks_conflict_without_persisting(
    activity_client: TestClient,
    activity_sessions: sessionmaker[Session],
    activity_records: dict[str, int],
) -> None:
    with activity_sessions() as db:
        before = db.query(Meeting).count()
    response = activity_client.post(
        "/api/meetings",
        json={
            "customer_id": activity_records["customer"],
            "scheduled_at": "2026-10-02T10:30:00Z",
            "scheduled_timezone": "UTC",
            "duration": 30,
        },
    )
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "schedule_conflict"
    assert body["details"]["conflicts"][0]["activity_type"] == "meeting"
    assert body["details"]["suggestions"]
    with activity_sessions() as db:
        assert db.query(Meeting).count() == before


def test_workspace_lock_query_compiles_with_mysql_for_update() -> None:
    compiled = str(
        _workspace_schedule_lock_statement().compile(dialect=mysql_dialect())
    )
    assert "scheduling_locks" in compiled
    assert "FOR UPDATE" in compiled


def test_manual_calls_and_followups_reject_cross_type_conflicts_and_allow_reschedule(
    activity_client: TestClient,
    activity_records: dict[str, int],
) -> None:
    conflict_time = "2026-10-02T10:30:00Z"
    call_create = activity_client.post(
        "/api/calls",
        json={
            "customer_id": activity_records["customer"],
            "scheduled_at": conflict_time,
            "scheduled_timezone": "UTC",
            "status": "scheduled",
        },
    )
    followup_create = activity_client.post(
        "/api/followups",
        json={
            "customer_id": activity_records["customer"],
            "type": "email",
            "due_date": conflict_time,
            "due_timezone": "UTC",
        },
    )
    assert call_create.status_code == 409
    assert followup_create.status_code == 409
    assert call_create.json()["error"]["code"] == "schedule_conflict"
    assert followup_create.json()["error"]["code"] == "schedule_conflict"

    call_update = activity_client.put(
        f"/api/calls/{activity_records['call']}",
        json={
            "scheduled_at": conflict_time,
            "scheduled_timezone": "UTC",
            "status": "scheduled",
        },
    )
    followup_update = activity_client.put(
        f"/api/followups/{activity_records['followup']}",
        json={"due_date": conflict_time, "due_timezone": "UTC"},
    )
    assert call_update.status_code == 409
    assert followup_update.status_code == 409

    meeting_reschedule = activity_client.put(
        f"/api/meetings/{activity_records['meeting']}",
        json={
            "scheduled_at": "2026-10-02T12:00:00Z",
            "scheduled_timezone": "UTC",
            "duration": 30,
        },
    )
    assert meeting_reschedule.status_code == 200

    cancelled_call = activity_client.post(
        "/api/calls",
        json={
            "customer_id": activity_records["customer"],
            "scheduled_at": conflict_time,
            "scheduled_timezone": "UTC",
            "status": "cancelled",
        },
    )
    assert cancelled_call.status_code == 201
