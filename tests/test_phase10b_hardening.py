from collections.abc import Sequence
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model import create_chat_model, is_quota_or_rate_limit_error
from app.core.config import Settings, settings
from app.core.exceptions import APIError
from app.core.logging_utils import contains_sensitive_material, redact_sensitive_text
from app.core.rate_limit import InMemorySlidingWindowRateLimiter, ai_rate_limiter
from app.main import create_app
from app.models import Call, Company, Customer, FollowUp, HitlActionProposal, Meeting, User
from app.services import agent_action_service, ai_service
from app.services.agent_action_state import PendingActionStore, pending_actions


class ToolCallingFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "ToolCallingFakeChatModel":
        _ = tools, kwargs
        return self


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}
        ],
    )


def _install_fake_chat_model(
    monkeypatch: pytest.MonkeyPatch,
    *responses: AIMessage,
) -> ToolCallingFakeChatModel:
    model = ToolCallingFakeChatModel(responses=list(responses))
    monkeypatch.setattr("app.services.agent_service.create_chat_model", lambda: model)
    monkeypatch.setattr("app.services.agent_action_service.create_chat_model", lambda: model)
    return model


class QuotaExceededProviderError(Exception):
    status_code = 429

    def __init__(
        self,
        message: str = "Error code: 429 - insufficient_quota for sk-secret-live-key",
    ) -> None:
        super().__init__(message)


class GenericProviderError(Exception):
    status_code = 500

    def __init__(
        self,
        message: str = "Upstream provider failure with sk-secret-live-key",
    ) -> None:
        super().__init__(message)


# =====================================================================
# PRIORITY 1: Input Validation (VARCHAR/Text bounds, Numeric(12,2), IDs, non-null updates)
# =====================================================================


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/api/customers",
            {
                "company": {"company_name": "A" * 201},
                "customer": {"customer_name": "Valid Customer"},
                "contacts": [{"name": "Valid Contact"}],
            },
        ),
        (
            "/api/customers",
            {
                "company": {"company_name": "Valid Co"},
                "customer": {"customer_name": "B" * 151},
                "contacts": [{"name": "Valid Contact"}],
            },
        ),
        (
            "/api/customers",
            {
                "company": {"company_name": "Valid Co"},
                "customer": {"customer_name": "Valid Customer"},
                "contacts": [{"name": "Valid Contact"}],
                "enquiry": {
                    "enquiry_text": "X" * 10001,
                },
            },
        ),
        (
            "/api/meetings",
            {
                "customer_id": 1,
                "scheduled_at": "2026-10-10T10:00:00Z",
                "agenda": "A" * 10001,
            },
        ),
        (
            "/api/calls",
            {
                "customer_id": 1,
                "call_type": "C" * 51,
                "scheduled_at": "2026-10-10T10:00:00Z",
            },
        ),
        (
            "/api/followups",
            {
                "customer_id": 1,
                "type": "T" * 51,
                "due_date": "2026-10-10T10:00:00Z",
            },
        ),
        (
            "/api/agent/chat",
            {
                "message": "M" * 4001,
            },
        ),
    ],
)
def test_oversized_strings_return_422(
    agent_client: TestClient,
    agent_records: dict[str, int],
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    response = agent_client.post(endpoint, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_value",
    [
        "-0.01",
        "-500.00",
        "10000000000.00",  # Exceeds Numeric(12, 2) max of 9999999999.99
        "123.456",  # More than 2 decimal places
    ],
)
def test_oversized_and_negative_numeric_values_return_422(
    agent_client: TestClient,
    agent_records: dict[str, int],
    invalid_value: str,
) -> None:
    response = agent_client.post(
        "/api/enquiries",
        json={
            "customer_id": agent_records["customer_id"],
            "product": "Enterprise Analytics",
            "enquiry_text": "Pricing validation test",
            "estimated_value": invalid_value,
        },
    )
    assert response.status_code == 422


def test_boundary_monetary_value_is_accepted(
    agent_client: TestClient,
    agent_records: dict[str, int],
) -> None:
    response = agent_client.post(
        "/api/enquiries",
        json={
            "customer_id": agent_records["customer_id"],
            "product": "Max Value Deal",
            "enquiry_text": "Boundary Numeric(12,2) value",
            "estimated_value": "9999999999.99",
        },
    )
    assert response.status_code == 201
    assert Decimal(str(response.json()["estimated_value"])) == Decimal("9999999999.99")


@pytest.mark.parametrize(
    ("method", "url_template", "id_key", "payload"),
    [
        ("PUT", "/api/meetings/{id}", "meeting_id", {"scheduled_at": None}),
        ("PUT", "/api/meetings/{id}", "meeting_id", {"status": None}),
        ("PUT", "/api/meetings/{id}", "meeting_id", {"customer_id": None}),
        ("PUT", "/api/calls/{id}", "call_id", {"customer_id": None}),
        ("PUT", "/api/calls/{id}", "call_id", {"status": None}),
        ("PUT", "/api/followups/{id}", "followup_id", {"type": None}),
        ("PUT", "/api/followups/{id}", "followup_id", {"due_date": None}),
        ("PUT", "/api/followups/{id}", "followup_id", {"status": None}),
        ("PATCH", "/api/customers/{id}", "customer_id", {"customer_name": None}),
        ("PATCH", "/api/customers/{id}", "customer_id", {"status": None}),
        ("PATCH", "/api/customers/{id}", "customer_id", {"sales_stage": None}),
        ("PATCH", "/api/contacts/{id}", "contact_id", {"name": None}),
        ("PATCH", "/api/contacts/{id}", "contact_id", {"is_primary": None}),
        ("PATCH", "/api/enquiries/{id}", "enquiry_id", {"enquiry_text": None}),
        ("PATCH", "/api/enquiries/{id}", "enquiry_id", {"priority": None}),
        ("PATCH", "/api/enquiries/{id}", "enquiry_id", {"status": None}),
    ],
)
def test_explicit_null_on_non_nullable_update_fields_returns_422(
    agent_client: TestClient,
    agent_records: dict[str, int],
    method: str,
    url_template: str,
    id_key: str,
    payload: dict[str, Any],
) -> None:
    url = url_template.format(id=agent_records[id_key])
    response = agent_client.request(method, url, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/api/meetings",
            {
                "customer_id": 0,
                "scheduled_at": "2026-10-10T10:00:00Z",
            },
        ),
        (
            "/api/calls",
            {
                "customer_id": -1,
                "call_type": "Outbound",
                "scheduled_at": "2026-10-10T10:00:00Z",
            },
        ),
        (
            "/api/followups",
            {
                "customer_id": 1,
                "enquiry_id": 0,
                "type": "call",
                "due_date": "2026-10-10T10:00:00Z",
            },
        ),
    ],
)
def test_non_positive_id_fields_return_422(
    agent_client: TestClient,
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    response = agent_client.post(endpoint, json=payload)
    assert response.status_code == 422


# =====================================================================
# PRIORITY 2: Timezone Normalization (+04:00 and UTC/naive across endpoints & agent proposals)
# =====================================================================


def test_timezone_normalization_for_meetings_calls_followups_and_agent_proposals(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]

    # 1. Meeting with +04:00 offset -> persisted as 08:00 UTC
    meeting_resp = agent_client.post(
        "/api/meetings",
        json={
            "customer_id": customer_id,
            "scheduled_at": "2026-10-15T12:00:00+04:00",
            "status": "scheduled",
            "agenda": "Timezone normalization check",
        },
    )
    assert meeting_resp.status_code == 201
    meeting_id = meeting_resp.json()["meeting_id"]

    # 2. Call with UTC 'Z' suffix -> persisted as 14:30 UTC
    call_resp = agent_client.post(
        "/api/calls",
        json={
            "customer_id": customer_id,
            "call_type": "Outbound",
            "scheduled_at": "2026-10-15T14:30:00Z",
            "status": "scheduled",
        },
    )
    assert call_resp.status_code == 201
    call_id = call_resp.json()["call_id"]

    # 3. Follow-up with naive datetime -> treated as UTC
    followup_resp = agent_client.post(
        "/api/followups",
        json={
            "customer_id": customer_id,
            "type": "email",
            "due_date": "2026-10-16T09:15:00",
            "status": "pending",
        },
    )
    assert followup_resp.status_code == 201
    followup_id = followup_resp.json()["followup_id"]

    with agent_sessions() as db:
        meeting = db.get(Meeting, meeting_id)
        call = db.get(Call, call_id)
        followup = db.get(FollowUp, followup_id)
        assert meeting is not None
        assert call is not None
        assert followup is not None
        assert meeting.scheduled_at.replace(tzinfo=timezone.utc) == datetime(
            2026, 10, 15, 8, 0, tzinfo=timezone.utc
        )
        assert call.scheduled_at.replace(tzinfo=timezone.utc) == datetime(
            2026, 10, 15, 14, 30, tzinfo=timezone.utc
        )
        assert followup.due_date.replace(tzinfo=timezone.utc) == datetime(
            2026, 10, 16, 9, 15, tzinfo=timezone.utc
        )

    # 4. Agent write proposal with +04:00 offset -> normalized to UTC in proposal payload
    _install_fake_chat_model(
        monkeypatch,
        _tool_call(
            "create_meeting",
            {
                "customer_id": customer_id,
                "scheduled_date": "2026-10-20",
                "scheduled_time": "16:00",
                "scheduled_timezone": "Asia/Dubai",
                "status": "scheduled",
                "agenda": "Offset proposal test",
            },
            call_id="call_tz_1",
        ),
    )

    chat_resp = agent_client.post(
        "/api/agent/chat",
        json={
            "message": "Schedule a meeting at 2026-10-20T16:00:00+04:00",
            "customer_id": customer_id,
        },
    )
    assert chat_resp.status_code == 200
    pending_action = chat_resp.json()["pending_action"]
    assert pending_action is not None
    assert pending_action["payload"]["scheduled_at"] == "2026-10-20T16:00:00+04:00"
    assert pending_action["payload"]["scheduled_timezone"] == "Asia/Dubai"


# =====================================================================
# PRIORITY 3 & 4: Server-Side Secret-Safe Logging & AI Failure Controls
# =====================================================================


def test_secret_redaction_never_logs_passwords_jwt_or_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_password", SecretStr("my-super-secret-db-pass-999"))
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("jwt-signing-secret-key-at-least-32-chars-long!"),
    )
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("AIzaSy-test-gemini-key"))
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-test-openai-legacy-key"))

    raw = (
        "Connection failed with password=my-super-secret-db-pass-999, "
        "token secret jwt-signing-secret-key-at-least-32-chars-long! "
        "AIzaSy-test-gemini-key "
        "and Bearer sk-proj-1234567890abcdefghijklmnop"
    )
    assert contains_sensitive_material(raw) is True
    redacted = redact_sensitive_text(raw)
    assert "my-super-secret-db-pass-999" not in redacted
    assert "jwt-signing-secret-key-at-least-32-chars-long!" not in redacted
    assert "AIzaSy-test-gemini-key" not in redacted
    assert "sk-test-openai-legacy-key" not in redacted
    assert "[REDACTED]" in redacted


def test_ai_provider_quota_vs_generic_error_mapping_and_safe_logging(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("AIzaSy-test-gemini-key"))
    customer_id = agent_records["customer_id"]

    class QuotaFailingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "QuotaFailingModel":
            return self

        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise QuotaExceededProviderError()

    quota_model = QuotaFailingModel(responses=[])
    monkeypatch.setattr("app.services.agent_service.create_chat_model", lambda: quota_model)
    monkeypatch.setattr("app.services.agent_action_service.create_chat_model", lambda: quota_model)

    with caplog.at_level(logging.WARNING):
        quota_resp = agent_client.post(
            "/api/agent/chat",
            json={"message": "Summarize customer", "customer_id": customer_id},
        )
    assert quota_resp.status_code == 503
    assert quota_resp.json()["error"]["code"] == "llm_quota_exceeded"
    assert "AIzaSy-test-gemini-key" not in quota_resp.text
    assert "Traceback" not in quota_resp.text
    for record in caplog.records:
        assert "AIzaSy-test-gemini-key" not in record.getMessage()

    caplog.clear()

    class GenericFailingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "GenericFailingModel":
            return self

        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise GenericProviderError()

    generic_model = GenericFailingModel(responses=[])
    monkeypatch.setattr("app.services.agent_service.create_chat_model", lambda: generic_model)
    monkeypatch.setattr("app.services.agent_action_service.create_chat_model", lambda: generic_model)

    with caplog.at_level(logging.ERROR):
        generic_resp = agent_client.post(
            "/api/agent/chat",
            json={"message": "Summarize customer", "customer_id": customer_id},
        )
    assert generic_resp.status_code == 502
    assert generic_resp.json()["error"]["code"] == "agent_error"
    assert "AIzaSy-test-gemini-key" not in generic_resp.text
    for record in caplog.records:
        assert "AIzaSy-test-gemini-key" not in record.getMessage()


def test_chat_gemini_configures_timeout_and_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("AIzaSy-test-config-only-key"))
    monkeypatch.setattr(settings, "gemini_timeout_seconds", 18.5)
    monkeypatch.setattr(settings, "gemini_max_retries", 4)

    captured_kwargs: dict[str, Any] = {}

    class DummyChatGemini:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        "langchain_google_genai.ChatGoogleGenerativeAI",
        DummyChatGemini,
    )
    create_chat_model()

    assert captured_kwargs["timeout"] == 18.5
    assert captured_kwargs["max_retries"] == 4
    assert captured_kwargs["model"] == settings.gemini_model
    assert captured_kwargs["google_api_key"] == settings.gemini_api_key
    assert is_quota_or_rate_limit_error(QuotaExceededProviderError()) is True
    assert is_quota_or_rate_limit_error(GenericProviderError()) is False
    assert is_quota_or_rate_limit_error(
        RuntimeError("RESOURCE_EXHAUSTED: requests per minute exceeded")
    ) is True


# =====================================================================
# PRIORITY 5: AI Request Rate Limiting
# =====================================================================


def test_ai_rate_limiting_blocks_excess_requests_and_returns_429(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_rate_limit_requests", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_window_seconds", 60)
    ai_rate_limiter.reset()

    _install_fake_chat_model(
        monkeypatch,
        AIMessage(content="First response"),
        AIMessage(content="Second response"),
        AIMessage(content="Third response"),
    )

    customer_id = agent_records["customer_id"]
    for _ in range(2):
        ok_resp = agent_client.post(
            "/api/agent/chat",
            json={"message": "Quick status check", "customer_id": customer_id},
        )
        assert ok_resp.status_code == 200

    limited_resp = agent_client.post(
        "/api/agent/chat",
        json={"message": "Third request over limit", "customer_id": customer_id},
    )
    assert limited_resp.status_code == 429
    assert limited_resp.json()["error"]["code"] == "rate_limit_exceeded"


def test_sliding_window_rate_limiter_unit_behavior() -> None:
    limiter = InMemorySlidingWindowRateLimiter()
    limiter.check("user:1", max_requests=2, window_seconds=10)
    limiter.check("user:1", max_requests=2, window_seconds=10)

    with pytest.raises(APIError) as exc_info:
        limiter.check("user:1", max_requests=2, window_seconds=10)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limit_exceeded"


# =====================================================================
# PRIORITY 6: Pending Action Persistence Capacity & Checkpoint Cleanup
# =====================================================================


def test_pending_action_store_capacity_does_not_evict_persisted_actions(
    agent_sessions: sessionmaker[Session],
) -> None:
    store = PendingActionStore(max_capacity=2)
    with agent_sessions() as db:
        user = User(
            email="hitl.capacity@example.com",
            full_name="HITL Capacity",
            password_hash="test-hash",
            role="sales",
            is_active=True,
        )
        db.add(user)
        db.commit()

        a1 = store.add(
            db,
            "create_meeting",
            {"customer_id": 1},
            "t1",
            user_id=user.user_id,
        )
        store.add(
            db,
            "create_meeting",
            {"customer_id": 1},
            "t2",
            user_id=user.user_id,
        )

        with pytest.raises(APIError) as exc_info:
            store.add(
                db,
                "create_meeting",
                {"customer_id": 1},
                "t3",
                user_id=user.user_id,
            )
        assert exc_info.value.status_code == 429

        claimed = store.claim(db, a1.action_id, user_id=user.user_id)
        assert claimed.action_id == a1.action_id


def test_langgraph_checkpoint_is_deleted_immediately_after_interrupt(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    _install_fake_chat_model(
        monkeypatch,
        _tool_call(
            "create_followup",
            {
                "customer_id": customer_id,
                "type": "call",
                "due_date": "2026-10-22T10:00:00Z",
                "status": "pending",
                "description": "Verify checkpoint cleanup",
            },
            call_id="call_cp_1",
        ),
    )

    delete_spy = MagicMock(wraps=agent_action_service.action_checkpointer.delete_thread)
    monkeypatch.setattr(agent_action_service.action_checkpointer, "delete_thread", delete_spy)

    resp = agent_client.post(
        "/api/agent/chat",
        json={"message": "Create a follow-up", "customer_id": customer_id},
    )
    assert resp.status_code == 200
    assert delete_spy.call_count >= 1
    assert len(agent_action_service.action_checkpointer.storage) == 0


def test_confirming_action_whose_underlying_record_became_invalid_marks_action_failed(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    followup_id = agent_records["followup_id"]

    _install_fake_chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_followups",
            {"customer_id": customer_id},
            call_id="read_fu_1",
        ),
        _tool_call(
            "complete_followup",
            {
                "customer_id": customer_id,
                "followup_id": followup_id,
            },
            call_id="call_complete_fu",
        ),
    )

    propose_resp = agent_client.post(
        "/api/agent/chat",
        json={"message": "Complete the follow-up", "customer_id": customer_id},
    )
    assert propose_resp.status_code == 200
    pending_action = propose_resp.json()["pending_action"]
    assert pending_action is not None
    action_id = pending_action["action_id"]

    # Delete the underlying follow-up record before confirmation so execution fails cleanly
    with agent_sessions() as db:
        fu = db.get(FollowUp, followup_id)
        assert fu is not None
        db.delete(fu)
        db.commit()

    confirm_resp = agent_client.post(f"/api/agent/actions/{action_id}/confirm")
    assert confirm_resp.status_code == 200
    body = confirm_resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "followup_not_found"

    # The durable failed action remains auditable and cannot be replayed.
    with agent_sessions() as db:
        action = db.get(HitlActionProposal, action_id)
        assert action is not None
        assert action.status == "failed"
        with pytest.raises(APIError) as exc_info:
            pending_actions.claim(db, action_id, user_id=1)
        assert exc_info.value.status_code == 404


# =====================================================================
# PRIORITY 7: Database Duplicate Protection
# =====================================================================


def test_database_unique_constraints_enforce_company_and_customer_uniqueness(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        duplicate_company = Company(company_name="ABC Trading", industry="Duplicate")
        db.add(duplicate_company)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        company = db.scalar(select(Company).where(Company.company_name == "ABC Trading"))
        assert company is not None
        duplicate_customer = Customer(
            company_id=company.company_id,
            customer_name="ABC Trading Customer",
            status="active",
            sales_stage="lead",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(duplicate_customer)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# =====================================================================
# PRIORITY 8: AI Snapshot Query Efficiency & Error Status Preservation
# =====================================================================


def test_customer_snapshot_does_not_execute_duplicate_queries_and_preserves_500_on_db_error(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    invoked_tools: list[str] = []

    with agent_sessions() as db:
        real_call_crm_tool = ai_service._call_crm_tool

        def counting_call_crm_tool(
            tools: dict[str, Any],
            name: str,
            payload: dict[str, Any],
        ) -> Any:
            invoked_tools.append(name)
            return real_call_crm_tool(tools, name, payload)

        monkeypatch.setattr(ai_service, "_call_crm_tool", counting_call_crm_tool)
        snapshot = ai_service._customer_snapshot(db, customer_id)

        assert snapshot["customer"]["customer_id"] == customer_id
        # Verify get_sales_enquiries and get_customer_activity are NOT redundantly invoked
        assert "get_sales_enquiries" not in invoked_tools
        assert "get_customer_activity" not in invoked_tools
        assert invoked_tools == [
            "get_customer_profile",
            "get_customer_meetings",
            "get_customer_calls",
            "get_customer_followups",
        ]


def test_ai_service_maps_database_error_to_500_not_404() -> None:
    assert ai_service.TOOL_ERROR_STATUS["database_error"] == 500
    assert ai_service.TOOL_ERROR_STATUS["customer_not_found"] == 404


# =====================================================================
# PRIORITY 9: Deployment / Health / Config Hardening & /analytics Route
# =====================================================================


def test_health_and_readiness_endpoints(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    liveness = agent_client.get("/health")
    assert liveness.status_code == 200
    assert liveness.json() == {"status": "ok"}

    monkeypatch.setattr("app.main.check_database_connection", lambda: True)
    ready_ok = agent_client.get("/health/ready")
    assert ready_ok.status_code == 200
    assert ready_ok.json() == {"status": "ready", "database": "ok"}

    monkeypatch.setattr("app.main.check_database_connection", lambda: False)
    ready_fail = agent_client.get("/health/ready")
    assert ready_fail.status_code == 503
    assert ready_fail.json()["error"]["code"] == "database_unavailable"


def test_docs_and_redoc_disabled_in_production_and_analytics_html_served(
    agent_client: TestClient,
) -> None:
    analytics_page = agent_client.get("/analytics")
    assert analytics_page.status_code == 200
    assert "text/html" in analytics_page.headers["content-type"]

    prod_settings = Settings(
        environment="production",
        jwt_secret_key=SecretStr("production-secret-key-with-at-least-32-characters"),
        cors_allowed_origins=["https://crm.example.com"],
        allow_public_registration=False,
    )
    prod_app = create_app(prod_settings)
    with TestClient(prod_app) as prod_client:
        assert prod_client.get("/docs").status_code == 404
        assert prod_client.get("/redoc").status_code == 404
        assert prod_client.get("/openapi.json").status_code == 404


# =====================================================================
# PRIORITY 12: Contacts, Enquiries, and Customer Update CRUD Endpoints
# =====================================================================


def test_contacts_enquiries_and_customer_update_crud_endpoints(
    agent_client: TestClient,
    agent_records: dict[str, int],
) -> None:
    customer_id = agent_records["customer_id"]

    # Update customer via PATCH and PUT
    patch_cust = agent_client.patch(
        f"/api/customers/{customer_id}",
        json={"sales_stage": "proposal"},
    )
    assert patch_cust.status_code == 200
    assert patch_cust.json()["sales_stage"] == "proposal"

    put_cust = agent_client.put(
        f"/api/customers/{customer_id}",
        json={
            "customer_name": "ABC Trading Enterprise",
            "status": "active",
            "sales_stage": "negotiation",
            "lead_source": "Partner Referral",
        },
    )
    assert put_cust.status_code == 200
    assert put_cust.json()["customer_name"] == "ABC Trading Enterprise"
    assert put_cust.json()["sales_stage"] == "negotiation"

    # Create, list, get, update Contact
    create_contact = agent_client.post(
        "/api/contacts",
        json={
            "customer_id": customer_id,
            "name": "Priya Nair",
            "job_title": "VP Procurement",
            "email": "priya.nair@example.com",
            "phone": "+971500001111",
            "is_primary": True,
        },
    )
    assert create_contact.status_code == 201
    new_contact_id = create_contact.json()["contact_id"]
    assert create_contact.json()["is_primary"] is True

    list_contacts = agent_client.get(f"/api/customers/{customer_id}/contacts")
    assert list_contacts.status_code == 200
    contacts_list = list_contacts.json()
    assert len(contacts_list) == 2
    primary_contacts = [item for item in contacts_list if item["is_primary"]]
    assert len(primary_contacts) == 1
    assert primary_contacts[0]["contact_id"] == new_contact_id

    patch_contact = agent_client.patch(
        f"/api/contacts/{new_contact_id}",
        json={"job_title": "Chief Procurement Officer"},
    )
    assert patch_contact.status_code == 200
    assert patch_contact.json()["job_title"] == "Chief Procurement Officer"

    # Create, list, get, update Sales Enquiry
    create_enquiry = agent_client.post(
        "/api/enquiries",
        json={
            "customer_id": customer_id,
            "product": "AI Copilot Module",
            "enquiry_text": "Interested in rolling out AI Copilot to 40 reps.",
            "estimated_value": "48000.00",
            "priority": "high",
            "status": "open",
        },
    )
    assert create_enquiry.status_code == 201
    new_enquiry_id = create_enquiry.json()["enquiry_id"]

    list_enquiries = agent_client.get(f"/api/customers/{customer_id}/enquiries")
    assert list_enquiries.status_code == 200
    assert len(list_enquiries.json()) == 2

    patch_enquiry = agent_client.patch(
        f"/api/enquiries/{new_enquiry_id}",
        json={"status": "in_progress", "estimated_value": "52000.00"},
    )
    assert patch_enquiry.status_code == 200
    assert patch_enquiry.json()["status"] == "in_progress"
    assert Decimal(str(patch_enquiry.json()["estimated_value"])) == Decimal("52000.00")
