from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.agent.model import create_chat_model
from app.services.ai_service import (
    MAX_PREVIOUS_DISCUSSIONS,
    _discussion_lines,
    _limit,
)
from app.models import Company, Customer, Meeting
from app.schemas.agent import CustomerSummaryOutput, MeetingBriefOutput


class StructuredFakeModel:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def with_structured_output(self, schema: type, **kwargs: Any) -> "StructuredFakeModel":
        _ = kwargs
        self.schema = schema
        return self

    def invoke(self, messages: Any) -> Any:
        _ = messages
        if self.error is not None:
            raise self.error
        if self.payload is None:
            return {}
        if self.payload == "raw-invalid":
            class _Message:
                content = "this is not structured json"
            return _Message()
        return self.payload


VALID_SUMMARY = CustomerSummaryOutput(
    summary="ABC Trading Customer is qualified with an open CRM enquiry.",
    key_points=["Sales stage is qualified.", "Open enquiry for a CRM subscription."],
    customer_concerns=["Interested in a CRM subscription"],
    recommended_next_action="Recommendation: send the pending proposal.",
)

VALID_BRIEF = MeetingBriefOutput(
    brief="Prepare to discuss the CRM subscription enquiry.",
    customer_overview="ABC Trading Customer is an active qualified account.",
    current_requirement="CRM subscription",
    unresolved_issues=["Proposal has not been sent."],
    recommended_talking_points=["Confirm budget and timeline."],
    recommended_next_action="Recommendation: agree next steps for the proposal.",
)


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: StructuredFakeModel) -> None:
    monkeypatch.setattr("app.services.ai_service.create_chat_model", lambda: model)


def test_discussions_are_sorted_across_meetings_and_calls_before_limiting() -> None:
    meetings = [
        {
            "scheduled_at": f"2026-10-{day:02d}T09:00:00+00:00",
            "agenda": f"Meeting discussion {day}",
        }
        for day in range(1, MAX_PREVIOUS_DISCUSSIONS + 2)
    ]
    calls = [
        {
            "actual_time": "2026-10-20T09:00:00+00:00",
            "outcome": "Most recent call discussion",
        }
    ]

    discussions = _discussion_lines(meetings, calls)

    assert len(discussions) == MAX_PREVIOUS_DISCUSSIONS
    assert discussions[0] == "Call on 2026-10-20T09:00:00+00:00: Most recent call discussion"
    assert discussions[1] == "Meeting on 2026-10-09T09:00:00+00:00: Meeting discussion 9"
    assert all("Meeting discussion 1" not in item for item in discussions)


def test_record_limit_supports_mapping_and_string_sequences() -> None:
    assert _limit(["a", "b"], 1) == ["a"]
    assert _limit([{"item": 1}, {"item": 2}], 1) == [{"item": 1}]


def test_chat_model_factory_uses_pydantic_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = SecretStr("test-key-never-used-for-a-request")
    monkeypatch.setattr(settings, "gemini_api_key", configured_key)

    model = create_chat_model()

    assert model.google_api_key == configured_key
    assert model.model == settings.gemini_model
    assert model.with_structured_output(CustomerSummaryOutput) is not None


def test_customer_summary_valid_customer(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(
        monkeypatch,
        StructuredFakeModel(
            {
                **VALID_SUMMARY.model_dump(),
                "open_followups": ["Invented follow-up"],
            }
        ),
    )

    response = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == agent_records["customer_id"]
    assert body["summary"] == VALID_SUMMARY.summary
    assert body["key_points"] == VALID_SUMMARY.key_points
    assert body["customer_concerns"] == VALID_SUMMARY.customer_concerns
    assert body["recommended_next_action"] == VALID_SUMMARY.recommended_next_action
    assert any("Send proposal" in item for item in body["open_followups"])
    assert "Invented follow-up" not in body["open_followups"]
    assert body["generated_at"]
    assert "api_key" not in body


def test_customer_summary_customer_not_found(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ai_service.create_chat_model",
        lambda: pytest.fail("The model must not run for an unknown customer."),
    )
    response = agent_client.post("/api/agent/customer-summary/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "customer_not_found"


def test_customer_summary_missing_llm_configuration(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)
    response = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert "API key" not in response.text


def test_customer_summary_model_failure(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(
        monkeypatch,
        StructuredFakeModel(error=RuntimeError("provider leaked secret")),
    )
    response = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
    assert "secret" not in response.text


def test_customer_summary_malformed_ai_output(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel({"summary": ""}))
    response = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "malformed_ai_output"


def test_customer_summary_no_activity_history(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    with agent_sessions() as db:
        company = Company(company_name="Quiet Co")
        db.add(company)
        db.flush()
        customer = Customer(
            company=company,
            customer_name="Quiet Customer",
            status="active",
            sales_stage="new",
            created_at=now,
            updated_at=now,
        )
        db.add(customer)
        db.commit()
        customer_id = customer.customer_id

    _patch_model(
        monkeypatch,
        StructuredFakeModel(
            CustomerSummaryOutput(
                summary="Quiet Customer has no recorded activity.",
                key_points=["No meetings, calls, or enquiries are available."],
                customer_concerns=[],
                recommended_next_action="Recommendation: schedule a discovery call.",
            )
        ),
    )
    response = agent_client.post(f"/api/agent/customer-summary/{customer_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["open_followups"] == []
    assert body["customer_concerns"] == []
    assert "no recorded activity" in body["summary"].lower() or "Quiet Customer" in body["summary"]


def test_customer_summary_response_structure(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel(VALID_SUMMARY))
    body = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    ).json()
    assert set(body) == {
        "customer_id",
        "summary",
        "key_points",
        "customer_concerns",
        "open_followups",
        "recommended_next_action",
        "generated_at",
    }


def test_meeting_brief_valid_meeting(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel(VALID_BRIEF))
    response = agent_client.post(
        f"/api/agent/meeting-brief/{agent_records['meeting_id']}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meeting_id"] == agent_records["meeting_id"]
    assert body["customer_id"] == agent_records["customer_id"]
    assert body["brief"] == VALID_BRIEF.brief
    assert body["customer_overview"] == VALID_BRIEF.customer_overview
    assert body["current_requirement"] == VALID_BRIEF.current_requirement
    assert any("Product demonstration" in item or "Interested" in item for item in body["previous_discussions"])
    assert body["recommended_talking_points"] == VALID_BRIEF.recommended_talking_points
    assert body["recommended_next_action"] == VALID_BRIEF.recommended_next_action
    assert body["generated_at"]


def test_meeting_brief_meeting_not_found(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ai_service.create_chat_model",
        lambda: pytest.fail("The model must not run for an unknown meeting."),
    )
    response = agent_client.post("/api/agent/meeting-brief/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "meeting_not_found"


def test_meeting_brief_associated_customer_data(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel(VALID_BRIEF))
    body = agent_client.post(
        f"/api/agent/meeting-brief/{agent_records['meeting_id']}"
    ).json()
    assert body["customer_id"] == agent_records["customer_id"]
    assert body["previous_discussions"]


def test_meeting_brief_missing_optional_contact_and_enquiry(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    with agent_sessions() as db:
        company = Company(company_name="Sparse Co")
        db.add(company)
        db.flush()
        customer = Customer(
            company=company,
            customer_name="Sparse Customer",
            status="active",
            sales_stage="new",
            created_at=now,
            updated_at=now,
        )
        db.add(customer)
        db.flush()
        meeting = Meeting(
            customer=customer,
            scheduled_at=now,
            status="scheduled",
        )
        db.add(meeting)
        db.commit()
        meeting_id = meeting.meeting_id
        customer_id = customer.customer_id

    invented = MeetingBriefOutput(
        brief="Keep the discussion factual.",
        customer_overview="Sparse Customer is a new account.",
        current_requirement="Invented product demand",
        unresolved_issues=[],
        recommended_talking_points=["Ask what they need."],
        recommended_next_action="Recommendation: gather requirements.",
    )
    _patch_model(monkeypatch, StructuredFakeModel(invented))
    response = agent_client.post(f"/api/agent/meeting-brief/{meeting_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == customer_id
    assert body["previous_discussions"] == []
    assert "unavailable" in body["current_requirement"].lower()


def test_meeting_brief_model_failure(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel(error=RuntimeError("internal provider")))
    response = agent_client.post(
        f"/api/agent/meeting-brief/{agent_records['meeting_id']}"
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
    assert "internal provider" not in response.text


def test_meeting_brief_structured_output_validation(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_model(monkeypatch, StructuredFakeModel({"brief": "only one field"}))
    response = agent_client.post(
        f"/api/agent/meeting-brief/{agent_records['meeting_id']}"
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "malformed_ai_output"


def test_openai_key_blank_is_treated_as_missing(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("  "))
    response = agent_client.post(
        f"/api/agent/customer-summary/{agent_records['customer_id']}"
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
