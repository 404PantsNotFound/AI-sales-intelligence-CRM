from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base, get_db
from app.main import app
from app.models import Company, Contact, Customer, SalesEnquiry
from tests.conftest import authorize_test_client


@pytest.fixture
def test_engine() -> Generator[Engine, None, None]:
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
def test_session_factory(test_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture
def client(test_session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        with test_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        authorize_test_client(test_session_factory, test_client)
        yield test_client
    app.dependency_overrides.pop(get_db, None)


def valid_registration(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "customer_name": "Ada Lovelace",
        "status": "active",
        "sales_stage": "qualified",
        "company": {
            "company_name": "Analytical Engines Ltd",
            "industry": "Technology",
            "city": "London",
        },
        "primary_contact": {
            "name": "Ada Lovelace",
            "job_title": "Director",
            "email": "ada@example.com",
            "phone": "+44 20 1234 5678",
        },
        "sales_enquiry": {
            "product": "CRM",
            "enquiry_text": "Interested in a team subscription.",
            "priority": "high",
            "status": "open",
            "estimated_value": "1250.50",
        },
    }
    for key, value in overrides.items():
        data[key] = value
    return data


def test_valid_customer_registration_creates_all_records(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    response = client.post("/api/customers", json=valid_registration())

    assert response.status_code == 201
    result = response.json()
    assert result["message"] == "Customer registered successfully"
    assert result["customer"]["customer_name"] == "Ada Lovelace"
    assert result["company"]["company_name"] == "Analytical Engines Ltd"
    assert result["contact"]["is_primary"] is True
    assert result["sales_enquiry"]["estimated_value"] == "1250.50"

    with test_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Company)) == 1
        assert db.scalar(select(func.count()).select_from(Customer)) == 1
        assert db.scalar(select(func.count()).select_from(Contact)) == 1
        assert db.scalar(select(func.count()).select_from(SalesEnquiry)) == 1
        customer = db.get(Customer, result["customer"]["customer_id"])
        contact = db.scalar(select(Contact))
        enquiry = db.scalar(select(SalesEnquiry))
        assert customer is not None
        assert contact is not None and contact.customer_id == customer.customer_id
        assert enquiry is not None and enquiry.customer_id == customer.customer_id
        assert contact.is_primary is True
        assert customer.created_at is not None
        assert contact.created_at is not None
        assert enquiry.created_at is not None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("customer_name",), " "),
        (("company", "company_name"), " "),
        (("primary_contact", "name"), ""),
        (("sales_enquiry", "enquiry_text"), " "),
    ],
)
def test_required_registration_fields_reject_blank_values(
    client: TestClient,
    path: tuple[str, ...],
    value: str,
) -> None:
    data = valid_registration()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    response = client.post("/api/customers", json=data)

    assert response.status_code == 422


def test_invalid_contact_email_is_rejected(client: TestClient) -> None:
    data = valid_registration()
    data["primary_contact"]["email"] = "not-an-email"

    assert client.post("/api/customers", json=data).status_code == 422


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("sales_enquiry", "priority"), "critical-ish"),
        (("status",), "pending"),
        (("sales_stage",), "unqualified"),
        (("sales_enquiry", "status"), "pending"),
    ],
)
def test_registration_rejects_undefined_enum_values(
    client: TestClient,
    path: tuple[str, ...],
    value: str,
) -> None:
    data = valid_registration()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert client.post("/api/customers", json=data).status_code == 422


def test_negative_estimated_value_is_rejected(client: TestClient) -> None:
    data = valid_registration()
    data["sales_enquiry"]["estimated_value"] = "-0.01"

    assert client.post("/api/customers", json=data).status_code == 422


def test_duplicate_company_is_reused_for_another_customer(client: TestClient) -> None:
    first = client.post("/api/customers", json=valid_registration())
    second_data = valid_registration(customer_name="Grace Hopper")
    second_data["company"]["company_name"] = "  ANALYTICAL ENGINES LTD "

    second = client.post("/api/customers", json=second_data)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["company"]["company_id"] == first.json()["company"]["company_id"]


def test_duplicate_customer_under_company_returns_conflict(client: TestClient) -> None:
    assert client.post("/api/customers", json=valid_registration()).status_code == 201

    response = client.post("/api/customers", json=valid_registration(customer_name=" ADA LOVELACE "))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_customer"


def test_failed_enquiry_flush_rolls_back_entire_registration(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    def fail_on_sales_enquiry(
        session: Session,
        flush_context: object,
        instances: object,
    ) -> None:
        del flush_context, instances
        if any(isinstance(item, SalesEnquiry) for item in session.new):
            raise SQLAlchemyError("test-only database failure")

    event.listen(Session, "before_flush", fail_on_sales_enquiry)
    try:
        response = client.post("/api/customers", json=valid_registration())
    finally:
        event.remove(Session, "before_flush", fail_on_sales_enquiry)

    assert response.status_code == 500
    assert "test-only database failure" not in response.text
    with test_session_factory() as db:
        for model in (Company, Customer, Contact, SalesEnquiry):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_get_customer_returns_company_contacts_and_enquiries(client: TestClient) -> None:
    created = client.post("/api/customers", json=valid_registration()).json()

    response = client.get(f"/api/customers/{created['customer']['customer_id']}")

    assert response.status_code == 200
    result = response.json()
    assert result["customer_name"] == "Ada Lovelace"
    assert result["company"]["company_name"] == "Analytical Engines Ltd"
    assert result["contacts"][0]["is_primary"] is True
    assert result["sales_enquiries"][0]["enquiry_text"] == "Interested in a team subscription."


def test_get_missing_customer_returns_404(client: TestClient) -> None:
    response = client.get("/api/customers/999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "customer_not_found"


def test_list_customers_returns_paginated_shape(client: TestClient) -> None:
    client.post("/api/customers", json=valid_registration())
    client.post("/api/customers", json=valid_registration(customer_name="Grace Hopper"))

    response = client.get("/api/customers")

    assert response.status_code == 200
    result = response.json()
    assert result["page"] == 1
    assert result["page_size"] == 20
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert result["items"][0]["company"]["company_name"] == "Analytical Engines Ltd"


@pytest.mark.parametrize(
    ("parameter", "query", "expected_name", "expected_total"),
    [
        ("search", "Analytical Engines", "Ada Lovelace", 2),
        ("search", "Grace", "Grace Hopper", 1),
        ("customer_name", "Ada", "Ada Lovelace", 1),
        ("company_name", "Analytical", "Ada Lovelace", 2),
    ],
)
def test_customer_search_filters_results(
    client: TestClient,
    parameter: str,
    query: str,
    expected_name: str,
    expected_total: int,
) -> None:
    client.post("/api/customers", json=valid_registration())
    client.post("/api/customers", json=valid_registration(customer_name="Grace Hopper"))

    response = client.get("/api/customers", params={parameter: query})

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == expected_total
    assert expected_name in {item["customer_name"] for item in result["items"]}


def test_customer_list_paginates_and_limits_page_size(client: TestClient) -> None:
    for name in ("Ada Lovelace", "Grace Hopper", "Katherine Johnson"):
        client.post("/api/customers", json=valid_registration(customer_name=name))

    first_page = client.get("/api/customers", params={"page": 1, "page_size": 2}).json()
    second_page = client.get("/api/customers", params={"page": 2, "page_size": 2}).json()
    invalid_page_size = client.get("/api/customers", params={"page_size": 101})

    assert first_page["total"] == 3
    assert len(first_page["items"]) == 2
    assert second_page["page"] == 2
    assert len(second_page["items"]) == 1
    assert invalid_page_size.status_code == 422
