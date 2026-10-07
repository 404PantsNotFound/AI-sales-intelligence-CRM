import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import APIError, api_error_handler
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/", "Build stronger"),
        ("/login", "Sign in to SalesDesk"),
        ("/customer-registration", "Register a customer"),
        ("/customer", "Customer intelligence"),
    ],
)
def test_frontend_pages_are_served(path: str, title: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert title in response.text
    assert "text/html" in response.headers["content-type"]


def test_frontend_assets_are_served() -> None:
    response = client.get("/static/js/api.js")

    assert response.status_code == 200
    assert "export function createCustomer" in response.text
    assert "export function generateCustomerSummary" in response.text
    assert "export function generateMeetingBrief" in response.text
    assert "/agent/customer-summary/" in response.text
    assert "/agent/meeting-brief/" in response.text


def test_customer_intelligence_js_wires_ai_endpoints() -> None:
    customer_js = client.get("/static/js/customer.js")
    assert customer_js.status_code == 200
    assert "generateCustomerSummary" in customer_js.text
    assert "generateMeetingBrief" in customer_js.text
    assert "Generate AI Summary" in customer_js.text
    assert "Prepare Me" in customer_js.text
    assert "aiSummaryError" in customer_js.text
    assert "Generating customer summary" in customer_js.text

    page = client.get("/customer")
    assert page.status_code == 200
    assert "meeting-brief-dialog" in page.text


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("/api/customers/test", "customers"),
        ("/api/enquiries/test", "enquiries"),
        ("/api/contacts/test", "contacts"),
        ("/api/meetings/test", "meetings"),
        ("/api/calls/test", "calls"),
        ("/api/followups/test", "followups"),
        ("/api/analytics/test", "analytics"),
        ("/api/agent/test", "agent"),
    ],
)
def test_module_endpoint(agent_client: TestClient, path: str, module: str) -> None:
    response = agent_client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "module": module}


def test_cors_allows_configured_frontend_origin() -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5500"


def test_application_errors_return_consistent_json() -> None:
    test_app = FastAPI()
    test_app.add_exception_handler(APIError, api_error_handler)

    @test_app.get("/failure")
    def raise_application_error() -> None:
        raise APIError("Expected failure", status_code=409, code="conflict")

    response = TestClient(test_app).get("/failure")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "conflict", "message": "Expected failure"}
    }


def test_database_dependency_is_configured_without_connecting() -> None:
    from app.database.connection import Base, SessionLocal, engine, get_db

    assert Base is not None
    assert SessionLocal is not None
    assert engine is not None
    assert engine.pool.checkedout() == 0
    db = next(get_db())
    try:
        assert db.is_active
    finally:
        db.close()
