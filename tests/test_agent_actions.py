from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.tools import AgentToolContext, build_read_only_tools
from app.agent.tools.action_schemas import CreateMeetingProposal
from app.core.config import settings
from app.core.exceptions import APIError
from app.database.connection import Base
from app.models import (
    Call,
    Company,
    Customer,
    FollowUp,
    HitlActionAudit,
    HitlActionProposal,
    HitlApprovalDecision,
    HitlExecutionAudit,
    HitlTask,
    Meeting,
    SalesEnquiry,
    SchedulingLock,
    User,
)
from app.schemas.agent import AgentChatRequest
from app.services import agent_action_service
from app.services.agent_action_state import PendingActionStore
from app.services.hitl_policy import set_action_policy
from tests.conftest import FROZEN_SCHEDULING_NOW, legacy_organization_id


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


def _chat_model(
    monkeypatch: pytest.MonkeyPatch,
    *responses: AIMessage,
) -> ToolCallingFakeChatModel:
    model = ToolCallingFakeChatModel(responses=list(responses))
    monkeypatch.setattr("app.services.agent_service.create_chat_model", lambda: model)
    return model


def _post_chat(
    client: TestClient,
    customer_id: int,
    message: str = "Please create this action",
) -> dict[str, Any]:
    response = client.post(
        "/api/agent/chat",
        json={
            "message": message,
            "customer_id": customer_id,
            "timezone_name": "Asia/Dubai",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _post_meeting_proposal(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    customer_id: int,
) -> dict[str, Any]:
    _chat_model(
        monkeypatch,
        _tool_call(
            "create_meeting",
            {
                "customer_id": customer_id,
                "scheduled_date": "2030-10-08",
                "scheduled_time": "15:00",
                "duration": 60,
                "agenda": "Discuss proposal",
            },
        ),
    )
    return _post_chat(client, customer_id, "Schedule a meeting on 2030-10-08 at 15:00")


def _post_meeting_clarification(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    customer_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    _chat_model(
        monkeypatch,
        _tool_call(
            "create_meeting",
            {"customer_id": customer_id, **values},
        ),
    )
    # The tool accepts only a time the current message states.
    stated_time = f" at {values['scheduled_time']}" if "scheduled_time" in values else ""
    return _post_chat(
        client,
        customer_id,
        f"Set up a meeting{stated_time} with this customer to discuss the latest enquiry.",
    )


def _count(sessions: sessionmaker[Session], model: type[Any]) -> int:
    with sessions() as db:
        return db.query(model).count()


def test_default_agent_tools_are_read_only(
    agent_sessions: sessionmaker[Session],
) -> None:
    with agent_sessions() as db:
        names = {tool.name for tool in build_read_only_tools(db, AgentToolContext(organization_id=legacy_organization_id(db)))}
    assert names.isdisjoint(
        {
            "create_meeting",
            "create_followup",
            "schedule_call",
            "record_call_result",
            "complete_followup",
        }
    )


def test_meeting_proposal_pauses_without_writing_then_confirms_once(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    proposal = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )
    pending = proposal["pending_action"]
    assert pending["action"] == "create_meeting"
    assert pending["payload"]["customer_id"] == agent_records["customer_id"]
    assert before == _count(agent_sessions, Meeting)

    confirmed = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["status"] == "completed"
    assert result["action"] == "create_meeting"
    assert _count(agent_sessions, Meeting) == before + 1

    second_confirmation = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert second_confirmation.status_code == 404
    assert _count(agent_sessions, Meeting) == before + 1


def test_scheduled_call_requires_confirmation_and_preserves_timezone(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Call)
    _chat_model(
        monkeypatch,
        _tool_call(
            "schedule_call",
            {
                "customer_id": agent_records["customer_id"],
                "scheduled_at": "2030-01-10T15:00:00+04:00",
                "scheduled_timezone": "Asia/Dubai",
                "duration": 30,
                "call_type": "Discovery",
            },
        ),
    )
    proposal = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Schedule a discovery call",
    )
    pending = proposal["pending_action"]
    assert pending["action"] == "schedule_call"
    assert before == _count(agent_sessions, Call)

    confirmed = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert _count(agent_sessions, Call) == before + 1
    with agent_sessions() as db:
        call = db.scalar(
            select(Call).where(
                Call.scheduled_at == datetime(2030, 1, 10, 11, 0)
            )
        )
        assert call is not None
        assert call.scheduled_timezone == "Asia/Dubai"
        assert call.duration == 30


def test_scheduled_call_rechecks_conflicts_at_confirmation(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Call)
    _chat_model(
        monkeypatch,
        _tool_call(
            "schedule_call",
            {
                "customer_id": agent_records["customer_id"],
                "scheduled_at": "2030-01-10T15:00:00+04:00",
                "scheduled_timezone": "Asia/Dubai",
                "duration": 30,
            },
        ),
    )
    proposal = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Schedule a discovery call",
    )
    pending = proposal["pending_action"]
    assert pending["action"] == "schedule_call"

    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=agent_records["customer_id"],
                scheduled_at=datetime(2030, 1, 10, 11, 0),
                duration=60,
                status="scheduled",
            )
        )
        db.commit()

    confirmed = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["status"] == "failed"
    assert result["error_code"] == "schedule_conflict"
    assert result["details"]["suggestions"]
    assert "Available alternatives" in result["message"]
    assert _count(agent_sessions, Call) == before


def test_meeting_without_date_or_time_creates_only_a_clarification_task(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_meetings = _count(agent_sessions, Meeting)
    before_proposals = _count(agent_sessions, HitlActionProposal)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {"agenda": "Discuss the latest enquiry."},
    )

    task = response["clarification_task"]
    assert response["response"] == (
        "Sure — what date and time would you like to schedule the meeting?"
    )
    assert task["status"] == "collecting_information"
    assert task["operations"][0]["missing_fields"] == [
        "scheduled_date",
        "scheduled_time",
    ]
    assert task["operations"][0]["values"]["scheduled_timezone"] == "Asia/Dubai"
    assert _count(agent_sessions, Meeting) == before_meetings
    assert _count(agent_sessions, HitlActionProposal) == before_proposals


def test_date_only_meeting_request_asks_only_for_time(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, HitlActionProposal)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {"scheduled_date": "2030-10-12", "agenda": "Discuss the latest enquiry."},
    )

    assert response["response"] == "What time would you like to schedule the meeting?"
    assert response["clarification_task"]["operations"][0]["values"][
        "scheduled_date"
    ] == "2030-10-12"
    assert response["clarification_task"]["operations"][0]["missing_fields"] == [
        "scheduled_time"
    ]
    assert _count(agent_sessions, HitlActionProposal) == before


def test_time_only_meeting_request_asks_only_for_date(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, HitlActionProposal)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {"scheduled_time": "15:00", "agenda": "Discuss the latest enquiry."},
    )

    assert response["response"] == "What date would you like to schedule the meeting?"
    assert response["clarification_task"]["operations"][0]["values"][
        "scheduled_time"
    ] == "15:00:00"
    assert response["clarification_task"]["operations"][0]["missing_fields"] == [
        "scheduled_date"
    ]
    assert _count(agent_sessions, HitlActionProposal) == before


def test_nonexistent_daylight_saving_meeting_time_requests_new_time(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_meetings = _count(agent_sessions, Meeting)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {
            "scheduled_date": "2026-03-08",
            "scheduled_time": "02:30",
            "scheduled_timezone": "America/New_York",
        },
    )

    operation = response["clarification_task"]["operations"][0]
    assert response["response"] == (
        "That local time does not exist because of a daylight-saving change. "
        "What valid time should I use?"
    )
    assert operation["missing_fields"] == ["valid_scheduled_time"]
    assert _count(agent_sessions, Meeting) == before_meetings


def test_ambiguous_daylight_saving_meeting_time_requires_occurrence_choice(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_meetings = _count(agent_sessions, Meeting)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {
            "scheduled_date": "2026-11-01",
            "scheduled_time": "01:30",
            "scheduled_timezone": "America/New_York",
        },
    )
    task = response["clarification_task"]
    operation = task["operations"][0]
    assert response["response"] == (
        "That local time occurs twice because of a daylight-saving change. "
        "Should I use the earlier or later occurrence?"
    )
    assert operation["missing_fields"] == ["scheduled_time_occurrence"]
    assert any(
        field["name"] == "scheduled_time_occurrence"
        and {choice["id"] for choice in field["choices"]} == {"earlier", "later"}
        for field in operation["fields"]
    )

    completed = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {"scheduled_time_occurrence": "later"},
        },
    )
    assert completed.status_code == 200, completed.text
    finalized = completed.json()["operations"][0]
    assert finalized["values"]["scheduled_at"] == "2026-11-01T01:30:00-05:00"
    assert finalized["values"]["scheduled_timezone"] == "America/New_York"
    assert _count(agent_sessions, Meeting) == before_meetings


def test_meeting_clarification_resumes_to_timezone_preserving_approval(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    response = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {"agenda": "Discuss the latest enquiry."},
    )
    task = response["clarification_task"]
    submitted = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": "2030-10-12",
                "scheduled_time": "15:00",
            },
        },
    )

    assert submitted.status_code == 200, submitted.text
    task_after_input = submitted.json()
    assert task_after_input["status"] == "awaiting_approval"
    operation = task_after_input["operations"][0]
    assert operation["status"] == "pending"
    assert operation["values"]["scheduled_at"] == "2030-10-12T15:00:00+04:00"
    assert operation["values"]["scheduled_timezone"] == "Asia/Dubai"
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, operation["action_id"])
        assert proposal is not None and proposal.status == "pending"
        assert proposal.parameters["scheduled_at"] == "2030-10-12T15:00:00+04:00"
        assert proposal.parameters["scheduled_timezone"] == "Asia/Dubai"
        assert db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == operation["action_id"]
            )
        ) is None

    approved = agent_client.post(
        f"/api/agent/actions/{operation['action_id']}/confirm"
    )
    assert approved.status_code == 200, approved.text
    meeting_response = approved.json()["record"]
    assert meeting_response["scheduled_at"] == "2030-10-12T15:00:00+04:00"
    assert meeting_response["scheduled_timezone"] == "Asia/Dubai"
    with agent_sessions() as db:
        meeting = db.scalar(select(Meeting).order_by(Meeting.meeting_id.desc()))
        assert meeting is not None
        assert meeting.scheduled_at == datetime(2030, 10, 12, 11, 0)
        assert meeting.scheduled_timezone == "Asia/Dubai"


def test_cancelled_meeting_proposal_has_no_database_effect(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    proposal = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )
    action_id = proposal["pending_action"]["action_id"]

    cancelled = agent_client.post(f"/api/agent/actions/{action_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _count(agent_sessions, Meeting) == before
    assert agent_client.post(f"/api/agent/actions/{action_id}/confirm").status_code == 404


def test_expired_action_cannot_be_confirmed(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    proposal = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )
    action_id = proposal["pending_action"]["action_id"]
    with agent_sessions() as db:
        record = db.get(HitlActionProposal, action_id)
        assert record is not None
        record.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()

    response = agent_client.post(f"/api/agent/actions/{action_id}/confirm")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "action_expired"
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, action_id)
        task = db.get(HitlTask, proposal.task_id) if proposal is not None else None
        assert proposal is not None and proposal.status == "expired"
        assert task is not None and task.status == "expired"


def test_followup_proposal_and_confirmation(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, FollowUp)
    _chat_model(
        monkeypatch,
        _tool_call(
            "get_sales_enquiries",
            {"customer_id": agent_records["customer_id"]},
            "read-enquiries",
        ),
        _tool_call(
            "create_followup",
            {
                "customer_id": agent_records["customer_id"],
                "enquiry_id": agent_records["enquiry_id"],
                "type": "proposal",
                "due_date": "2030-10-09T10:00:00+04:00",
                "description": "Send the requested proposal",
            },
        ),
    )
    proposal = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Create a follow-up for the proposal.",
    )

    assert proposal["pending_action"]["action"] == "create_followup"
    assert _count(agent_sessions, FollowUp) == before
    result = agent_client.post(
        f"/api/agent/actions/{proposal['pending_action']['action_id']}/confirm"
    )

    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["record"]["description"] == "Send the requested proposal"
    assert _count(agent_sessions, FollowUp) == before + 1


def test_record_call_result_is_proposed_then_written_without_placing_a_call(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Call)
    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_profile",
            {"customer_id": agent_records["customer_id"]},
            "read-profile",
        ),
        _tool_call(
            "record_call_result",
            {
                "customer_id": agent_records["customer_id"],
                "contact_id": agent_records["contact_id"],
                "outcome": "Requested pricing proposal",
                "notes": "Spoke about product pricing.",
            },
        ),
    )
    proposal = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Record the call result.",
    )

    assert proposal["pending_action"]["action"] == "record_call_result"
    assert _count(agent_sessions, Call) == before
    result = agent_client.post(
        f"/api/agent/actions/{proposal['pending_action']['action_id']}/confirm"
    )

    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["record"]["actual_time"] is None
    assert "No call was placed" in result.json()["message"]
    assert _count(agent_sessions, Call) == before + 1


def test_complete_followup_requires_read_then_confirm(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_followups",
            {"customer_id": agent_records["customer_id"]},
            "read-followups",
        ),
        _tool_call(
            "complete_followup",
            {
                "customer_id": agent_records["customer_id"],
                "followup_id": agent_records["followup_id"],
            },
            "complete-followup",
        ),
    )
    proposal = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Mark the retrieved follow-up complete.",
    )
    assert proposal["pending_action"]["action"] == "complete_followup"
    with agent_sessions() as db:
        followup = db.get(FollowUp, agent_records["followup_id"])
        assert followup is not None
        assert followup.status == "pending"

    response = agent_client.post(
        f"/api/agent/actions/{proposal['pending_action']['action_id']}/confirm"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    with agent_sessions() as db:
        followup = db.get(FollowUp, agent_records["followup_id"])
        assert followup is not None
        assert followup.status == "completed"
        assert followup.completed_at is not None


def test_action_rejects_unknown_customer_and_related_records(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    _chat_model(
        monkeypatch,
        _tool_call(
            "create_meeting",
            {
                "customer_id": agent_records["customer_id"],
                "contact_id": 999,
                "scheduled_date": "2030-10-08",
                "scheduled_time": "15:00",
            },
        ),
        AIMessage(content="I could not verify the requested contact in CRM records."),
    )
    response = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Schedule a meeting at 15:00 with an unverified contact.",
    )
    assert response["pending_action"] is None
    assert _count(agent_sessions, Meeting) == before


def test_invalid_action_id_fails_without_a_write(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
) -> None:
    before = _count(agent_sessions, Meeting)
    response = agent_client.post("/api/agent/actions/not-a-valid-action/confirm")
    assert response.status_code == 404
    assert _count(agent_sessions, Meeting) == before


def test_chat_request_validation_and_safe_missing_key(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(" "))
    response = agent_client.post(
        "/api/agent/chat",
        json={"message": "Please create a meeting", "customer_id": agent_records["customer_id"]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert "API key" not in response.text
    with pytest.raises(ValidationError):
        AgentChatRequest(message=" ", customer_id=agent_records["customer_id"])

def test_enrichment_proposal_does_not_write_before_confirmation(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    enquiry_id = agent_records["enquiry_id"]

    with agent_sessions() as db:
        customer = db.get(Customer, customer_id)
        enquiry = db.get(SalesEnquiry, enquiry_id)

        assert customer is not None
        assert enquiry is not None

        original_sales_stage = customer.sales_stage
        original_priority = enquiry.priority
        original_status = enquiry.status

    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_profile",
            {"customer_id": customer_id},
            "read-profile",
        ),
        _tool_call(
            "get_sales_enquiries",
            {"customer_id": customer_id},
            "read-enquiries",
        ),
        _tool_call(
            "apply_enrichment",
            {
                "customer_id": customer_id,
                "enquiry_id": enquiry_id,
                "sales_stage": "qualified",
                "enquiry_priority": "high",
                "enquiry_status": "in_progress",
            },
            "apply-enrichment",
        ),
    )

    proposal = _post_chat(
        agent_client,
        customer_id,
        "Analyze the CRM activity and enrich the customer.",
    )

    assert proposal["pending_action"] is not None
    assert proposal["pending_action"]["action"] == "apply_enrichment"

    with agent_sessions() as db:
        customer = db.get(Customer, customer_id)
        enquiry = db.get(SalesEnquiry, enquiry_id)

        assert customer is not None
        assert enquiry is not None

        assert customer.sales_stage == original_sales_stage
        assert enquiry.priority == original_priority
        assert enquiry.status == original_status


def test_confirmed_enrichment_updates_customer_and_enquiry(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    enquiry_id = agent_records["enquiry_id"]

    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_profile",
            {"customer_id": customer_id},
            "read-profile",
        ),
        _tool_call(
            "get_sales_enquiries",
            {"customer_id": customer_id},
            "read-enquiries",
        ),
        _tool_call(
            "apply_enrichment",
            {
                "customer_id": customer_id,
                "enquiry_id": enquiry_id,
                "sales_stage": "qualified",
                "customer_status": "active",
                "enquiry_priority": "high",
                "enquiry_status": "in_progress",
                "estimated_value": 25000,
            },
            "apply-enrichment",
        ),
    )

    proposal = _post_chat(
        agent_client,
        customer_id,
        "Enrich this customer from the retrieved CRM information.",
    )

    pending = proposal["pending_action"]

    assert pending is not None
    assert pending["action"] == "apply_enrichment"

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "completed"
    assert result["action"] == "apply_enrichment"
    assert result["message"] == "CRM enrichment applied successfully."

    with agent_sessions() as db:
        customer = db.get(Customer, customer_id)
        enquiry = db.get(SalesEnquiry, enquiry_id)

        assert customer is not None
        assert enquiry is not None

        assert customer.sales_stage == "qualified"
        assert customer.status == "active"
        assert enquiry.priority == "high"
        assert enquiry.status == "in_progress"
        assert enquiry.estimated_value == Decimal("25000")


def test_cancelled_enrichment_has_no_database_effect(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    enquiry_id = agent_records["enquiry_id"]

    with agent_sessions() as db:
        customer = db.get(Customer, customer_id)
        enquiry = db.get(SalesEnquiry, enquiry_id)

        assert customer is not None
        assert enquiry is not None

        original_sales_stage = customer.sales_stage
        original_priority = enquiry.priority

    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_profile",
            {"customer_id": customer_id},
            "read-profile",
        ),
        _tool_call(
            "get_sales_enquiries",
            {"customer_id": customer_id},
            "read-enquiries",
        ),
        _tool_call(
            "apply_enrichment",
            {
                "customer_id": customer_id,
                "enquiry_id": enquiry_id,
                "sales_stage": "proposal",
                "enquiry_priority": "urgent",
            },
            "apply-enrichment",
        ),
    )

    proposal = _post_chat(
        agent_client,
        customer_id,
        "Propose enrichment for this customer.",
    )

    pending = proposal["pending_action"]

    assert pending is not None

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/cancel"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    with agent_sessions() as db:
        customer = db.get(Customer, customer_id)
        enquiry = db.get(SalesEnquiry, enquiry_id)

        assert customer is not None
        assert enquiry is not None

        assert customer.sales_stage == original_sales_stage
        assert enquiry.priority == original_priority


def test_enrichment_requires_enquiry_id_for_enquiry_fields(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]

    _chat_model(
        monkeypatch,
        _tool_call(
            "get_customer_profile",
            {"customer_id": customer_id},
            "read-profile",
        ),
        _tool_call(
            "apply_enrichment",
            {
                "customer_id": customer_id,
                "enquiry_priority": "high",
            },
            "apply-enrichment",
        ),
    )

    proposal = _post_chat(
        agent_client,
        customer_id,
        "Enrich the enquiry priority.",
    )

    pending = proposal["pending_action"]
    assert pending is not None

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "failed"
    assert result["error_code"] == "enquiry_id_required"


def _configure_action_policy(
    sessions: sessionmaker[Session],
    action: str,
    mode: str,
) -> None:
    with sessions() as db:
        admin = User(
            email=f"policy-{action}-{mode}@example.com",
            full_name="Policy Administrator",
            password_hash="test-only-hash",
            role="admin",
            is_active=True,
            organization_id=legacy_organization_id(db),
        )
        db.add(admin)
        db.commit()
        set_action_policy(
            db, action, mode, organization_id=admin.organization_id, changed_by=admin.email
        )


def test_automatic_policy_still_requires_explicit_meeting_approval(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_action_policy(agent_sessions, "create_meeting", "automatic")
    before = _count(agent_sessions, Meeting)

    response = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )

    pending = response["pending_action"]
    assert pending is not None
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        assert proposal is not None
        assert proposal.status == "pending"
        assert db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == proposal.action_id
            )
        ) is None

    confirmed = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert _count(agent_sessions, Meeting) == before + 1
    with agent_sessions() as db:
        assert db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == pending["action_id"]
            )
        ) is not None


def test_disabled_policy_blocks_old_pending_action_before_crm_write(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    pending = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )["pending_action"]
    assert pending is not None
    _configure_action_policy(agent_sessions, "create_meeting", "disabled")

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "action_disabled"
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        assert proposal is not None and proposal.status == "rejected"
        decision = db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == pending["action_id"]
            )
        )
        assert decision is not None
        assert decision.decision == "rejected"
        audit = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == pending["action_id"],
                HitlActionAudit.event_type == "rejected",
            )
        )
        assert audit is not None
        assert audit.details == {"reason": "action_disabled"}


def test_policy_change_to_automatic_does_not_auto_execute_existing_proposal(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    pending = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )["pending_action"]
    assert pending is not None

    _configure_action_policy(agent_sessions, "create_meeting", "automatic")

    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        assert proposal is not None and proposal.status == "pending"

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert _count(agent_sessions, Meeting) == before + 1


def test_admin_reviewer_can_approve_another_users_pending_action(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    store = PendingActionStore()
    with agent_sessions() as db:
        owner = db.scalar(select(User).where(User.email == "sales.rep@example.com"))
        assert owner is not None
        reviewer = User(
            email="hitl.reviewer@example.com",
            full_name="HITL Reviewer",
            password_hash="test-only-hash",
            role="admin",
            is_active=True,
            organization_id=legacy_organization_id(db),
        )
        db.add(reviewer)
        db.commit()
        reviewer_id = reviewer.user_id
        pending = store.add(
            db,
            "create_meeting",
            CreateMeetingProposal(
                customer_id=agent_records["customer_id"],
                scheduled_at="2026-11-01T12:00:00+00:00",
                contact_id=agent_records["contact_id"],
                enquiry_id=agent_records["enquiry_id"],
                duration=60,
                status="scheduled",
                agenda="Admin-reviewed meeting",
            ).model_dump(mode="json"),
            "unused",
            user_id=owner.user_id,
        )

    with agent_sessions() as db:
        result = agent_action_service.confirm_action(
            db,
            pending.action_id,
            user_id=reviewer_id,
            is_admin=True,
        )
        assert result.status == "completed"

    with agent_sessions() as db:
        decision = db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == pending.action_id
            )
        )
        proposal = db.get(HitlActionProposal, pending.action_id)
        assert decision is not None and decision.decided_by_user_id == reviewer_id
        assert proposal is not None and proposal.owner_user_id == owner.user_id


def test_changed_proposal_parameters_invalidate_approval(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    pending = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )["pending_action"]
    assert pending is not None
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        assert proposal is not None
        proposal.parameters = {**proposal.parameters, "agenda": "Changed after review"}
        db.commit()

    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "action_parameters_changed"
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        task = db.get(HitlTask, proposal.task_id) if proposal is not None else None
        assert proposal is not None and proposal.status == "failed"
        assert task is not None and task.status == "failed"
        assert db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == pending["action_id"]
            )
        ) is None


def test_execution_failure_is_persisted_without_reporting_success(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )["pending_action"]
    assert pending is not None

    def fail_create(*args: Any, **kwargs: Any) -> Meeting:
        raise APIError("Simulated meeting validation failure.", 409, "meeting_conflict")

    monkeypatch.setattr("app.services.agent_action_service.meeting_service.create_meeting", fail_create)
    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "meeting_conflict"
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, pending["action_id"])
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == pending["action_id"]
            )
        )
        assert proposal is not None and proposal.status == "failed"
        assert execution is not None and execution.status == "failed"


def test_crm_write_rolls_back_if_success_audit_cannot_be_saved(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _count(agent_sessions, Meeting)
    pending = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )["pending_action"]
    assert pending is not None
    original_finish = agent_action_service.pending_actions.finish

    def fail_completion_audit(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("status") == "completed":
            raise APIError("Simulated audit persistence failure.", 500, "audit_failure")
        original_finish(*args, **kwargs)

    monkeypatch.setattr(
        agent_action_service.pending_actions,
        "finish",
        fail_completion_audit,
    )
    response = agent_client.post(
        f"/api/agent/actions/{pending['action_id']}/confirm"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "audit_failure"
    assert _count(agent_sessions, Meeting) == before
    with agent_sessions() as db:
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == pending["action_id"]
            )
        )
        assert execution is not None and execution.status == "failed"


def test_simultaneous_claims_only_start_one_execution(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'hitl-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(SchedulingLock(lock_id=1))
        db.commit()
    store = PendingActionStore()
    try:
        with sessions() as db:
            user = User(
                email="hitl.concurrent@example.com",
                full_name="Concurrent Test User",
                password_hash="test-only-hash",
                role="sales",
                is_active=True,
                organization_id=legacy_organization_id(db),
            )
            db.add(user)
            company = Company(organization_id=legacy_organization_id(db), company_name="Concurrency Test Company")
            db.add(company)
            db.flush()
            customer = Customer(
                company_id=company.company_id,
                customer_name="Concurrency Test Customer",
                status="active",
                sales_stage="qualified",
            )
            db.add(customer)
            db.commit()
            customer_id = customer.customer_id
            record = store.add(
                db,
                "create_meeting",
                CreateMeetingProposal(
                    customer_id=customer_id,
                    scheduled_at="2026-11-01T12:00:00+00:00",
                    duration=60,
                    status="scheduled",
                    agenda="Review",
                ).model_dump(mode="json"),
                "unused",
                user_id=user.user_id,
            )
            owner_id = user.user_id

        barrier = Barrier(2)

        def attempt_confirmation() -> str:
            with sessions() as db:
                barrier.wait()
                try:
                    result = agent_action_service.confirm_action(
                        db,
                        record.action_id,
                        user_id=owner_id,
                    )
                    return f"{result.status}:{result.error_code}:{result.message}"
                except APIError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt_confirmation(), range(2)))

        assert sum(value.startswith("completed:") for value in outcomes) == 1, outcomes
        assert len(outcomes) == 2
        assert all(
            value.startswith("completed:")
            or value in {"action_already_processed", "action_not_found"}
            for value in outcomes
        )
        with sessions() as db:
            proposal = db.get(HitlActionProposal, record.action_id)
            assert proposal is not None and proposal.status == "completed"
            assert db.query(Meeting).count() == 1
            assert db.query(HitlExecutionAudit).filter_by(
                action_id=record.action_id
            ).count() == 1
            assert db.query(HitlApprovalDecision).filter_by(
                action_id=record.action_id
            ).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

def test_chat_gives_model_request_timezone_and_labels_confirmation_with_offset(
    agent_client: TestClient,
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    real_build = agent_action_service.build_sales_agent

    class RecordingAgent:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def invoke(self, payload: dict[str, Any], **kwargs: Any) -> Any:
            prompts.append(payload["messages"][0]["content"])
            return self.agent.invoke(payload, **kwargs)

    monkeypatch.setattr(
        agent_action_service,
        "build_sales_agent",
        lambda *args, **kwargs: RecordingAgent(real_build(*args, **kwargs)),
    )

    proposal = _post_meeting_proposal(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
    )

    assert prompts and prompts[0].startswith(
        "Current time (UTC): 2026-10-01T00:00+00:00\n"
        "Earlier messages in this conversation: none. This is its first message.\n"
        "User's timezone (IANA): Asia/Dubai\n"
    )
    assert proposal["pending_action"]["payload"]["scheduled_timezone"] == "Asia/Dubai"
    assert proposal["response"] == (
        "I am ready to create meeting for 2030-10-08 15:00 Asia/Dubai (UTC+04:00). "
        "Please confirm."
    )


def test_chat_conflict_returns_checked_alternatives_and_selection_rechecks(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    busy_start = datetime(2030, 1, 10, 11, 0, tzinfo=timezone.utc)
    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=customer_id,
                scheduled_at=busy_start.replace(tzinfo=None),
                duration=60,
                status="scheduled",
            )
        )
        db.commit()
    _chat_model(
        monkeypatch,
        _tool_call(
            "schedule_call",
            {
                "customer_id": customer_id,
                "scheduled_at": "2030-01-10T15:00:00+04:00",
                "scheduled_timezone": "Asia/Dubai",
                "duration": 30,
            },
        ),
        AIMessage(content="That time conflicts with a meeting."),
    )
    before_calls = _count(agent_sessions, Call)
    before_proposals = _count(agent_sessions, HitlActionProposal)

    response = _post_chat(agent_client, customer_id, "Schedule a call")

    # The reply comes from the availability result, not the model's text.
    assert response["response"] == (
        "That time overlaps an existing scheduled meeting. No proposal was created. "
        "Choose one of the available times below."
    )
    assert response["pending_action"] is None
    task = response["clarification_task"]
    assert task["operations"][0]["action"] == "schedule_call"
    alternatives = response["schedule_alternatives"]
    assert alternatives
    for slot in alternatives:
        assert slot["timezone"] == "Asia/Dubai"
        start = datetime.fromisoformat(slot["starts_at"])
        assert start >= busy_start + timedelta(minutes=60) or (
            start + timedelta(minutes=30) <= busy_start
        )
    assert _count(agent_sessions, HitlActionProposal) == before_proposals

    # A slot taken after it was offered is rejected with fresh alternatives.
    stale = alternatives[0]
    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=customer_id,
                scheduled_at=datetime.fromisoformat(stale["starts_at"])
                .astimezone(timezone.utc)
                .replace(tzinfo=None),
                duration=30,
                status="scheduled",
            )
        )
        db.commit()
    rejected = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_at": stale["starts_at"],
                "scheduled_timezone": stale["timezone"],
                "scheduled_time_occurrence": None,
            },
        },
    )
    assert rejected.status_code == 409
    error = rejected.json()["error"]
    assert error["code"] == "schedule_conflict"
    fresh = error["details"]["suggestions"]
    assert fresh and stale["starts_at"] not in {slot["starts_at"] for slot in fresh}
    assert _count(agent_sessions, HitlActionProposal) == before_proposals

    accepted = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_at": fresh[0]["starts_at"],
                "scheduled_timezone": fresh[0]["timezone"],
                "scheduled_time_occurrence": None,
            },
        },
    )
    assert accepted.status_code == 200, accepted.text
    updated = accepted.json()
    assert updated["status"] == "awaiting_approval"
    assert updated["operations"][0]["action_id"]
    assert _count(agent_sessions, HitlActionProposal) == before_proposals + 1
    assert _count(agent_sessions, Call) == before_calls


PAST_AT = datetime(2026, 8, 7, 10, 45, tzinfo=timezone.utc)
FUTURE_AT = datetime(2030, 11, 5, 10, 45, tzinfo=timezone.utc)
SCHEDULE_CASES = {
    "create_meeting": (Meeting, "scheduled_at", ("completed", "cancelled", "no_show")),
    "schedule_call": (Call, "scheduled_at", ("completed", "cancelled", "missed")),
    "create_followup": (FollowUp, "due_date", ("completed", "cancelled")),
}


def _schedule_args(action: str, at: datetime) -> dict[str, Any]:
    if action == "create_meeting":
        return {
            "scheduled_date": at.date().isoformat(),
            "scheduled_time": at.strftime("%H:%M"),
            "scheduled_timezone": "UTC",
            "agenda": "Quarterly review",
        }
    if action == "schedule_call":
        return {"scheduled_at": at.isoformat(), "scheduled_timezone": "UTC", "duration": 30}
    return {"type": "call back", "due_date": at.isoformat(), "due_timezone": "UTC"}


def _suggestion_values(action: str, slot: dict[str, Any]) -> dict[str, Any]:
    if action == "create_meeting":
        return {
            "scheduled_date": slot["starts_at"][:10],
            "scheduled_time": slot["starts_at"][11:16],
            "scheduled_timezone": slot["timezone"],
            "scheduled_time_occurrence": None,
        }
    if action == "schedule_call":
        return {
            "scheduled_at": slot["starts_at"],
            "scheduled_timezone": slot["timezone"],
            "scheduled_time_occurrence": None,
        }
    return {
        "due_date": slot["starts_at"],
        "due_timezone": slot["timezone"],
        "due_time_occurrence": None,
    }


def _add_history(
    sessions: sessionmaker[Session],
    action: str,
    customer_id: int,
    at: datetime,
    statuses: Sequence[str],
) -> None:
    model, time_field, _ = SCHEDULE_CASES[action]
    with sessions() as db:
        for status in statuses:
            extra = {"type": "call back"} if model is FollowUp else {}
            db.add(
                model(
                    customer_id=customer_id,
                    status=status,
                    **{time_field: at.replace(tzinfo=None)},
                    **extra,
                )
            )
        db.commit()


@pytest.mark.parametrize("action", list(SCHEDULE_CASES))
def test_past_request_reports_history_then_offers_checked_future_times(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    customer_id = agent_records["customer_id"]
    model = SCHEDULE_CASES[action][0]
    _add_history(agent_sessions, action, customer_id, PAST_AT, ["completed"])
    tool_messages: list[str] = []
    real_build = agent_action_service.build_sales_agent

    class RecordingAgent:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def invoke(self, payload: dict[str, Any], **kwargs: Any) -> Any:
            state = self.agent.invoke(payload, **kwargs)
            tool_messages.extend(
                str(message.content)
                for message in state["messages"]
                if message.type == "tool"
            )
            return state

    monkeypatch.setattr(
        agent_action_service,
        "build_sales_agent",
        lambda *args, **kwargs: RecordingAgent(real_build(*args, **kwargs)),
    )
    _chat_model(
        monkeypatch,
        _tool_call(action, {"customer_id": customer_id, **_schedule_args(action, PAST_AT)}),
        AIMessage(content="That time has already passed."),
    )
    before_proposals = _count(agent_sessions, HitlActionProposal)
    before_records = _count(agent_sessions, model)

    response = _post_chat(agent_client, customer_id, "Schedule it on August 7 at 10:45")

    # Past time: no proposal and no suggested times yet; history is named as history.
    assert response["pending_action"] is None
    assert response["schedule_reason"] == "past"
    assert response["schedule_alternatives"] == []
    assert any(
        "already passed" in text
        and "completed historical record, not a scheduling conflict" in text
        for text in tool_messages
    ), tool_messages
    assert _count(agent_sessions, HitlActionProposal) == before_proposals
    task_id = response["clarification_task"]["task_id"]

    # "Schedule a new ..." resubmits the task; the server rejects the past time
    # and returns checked future slots.
    rejected = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={"operation_index": 0, "values": {}},
    )
    assert rejected.status_code == 422
    error = rejected.json()["error"]
    assert error["code"] == "schedule_in_past"
    slots = error["details"]["suggestions"]
    assert slots
    assert all(
        datetime.fromisoformat(slot["starts_at"]) > FROZEN_SCHEDULING_NOW
        for slot in slots
    )
    assert _count(agent_sessions, HitlActionProposal) == before_proposals

    accepted = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={"operation_index": 0, "values": _suggestion_values(action, slots[0])},
    )
    assert accepted.status_code == 200, accepted.text
    task = accepted.json()
    assert task["status"] == "awaiting_approval"
    assert _count(agent_sessions, model) == before_records

    confirmed = agent_client.post(
        f"/api/agent/actions/{task['operations'][0]['action_id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert _count(agent_sessions, model) == before_records + 1


@pytest.mark.parametrize("action", list(SCHEDULE_CASES))
def test_inactive_history_at_same_future_time_does_not_block_new_activity(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    customer_id = agent_records["customer_id"]
    _add_history(agent_sessions, action, customer_id, FUTURE_AT, SCHEDULE_CASES[action][2])
    _chat_model(
        monkeypatch,
        _tool_call(action, {"customer_id": customer_id, **_schedule_args(action, FUTURE_AT)}),
    )

    response = _post_chat(agent_client, customer_id, "Schedule it at 10:45")

    assert response["pending_action"]["action"] == action
    assert response["schedule_reason"] is None


@pytest.mark.parametrize("action", list(SCHEDULE_CASES))
def test_execution_rejects_approved_activity_whose_time_has_passed(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    customer_id = agent_records["customer_id"]
    model = SCHEDULE_CASES[action][0]
    _chat_model(
        monkeypatch,
        _tool_call(action, {"customer_id": customer_id, **_schedule_args(action, FUTURE_AT)}),
    )
    pending = _post_chat(agent_client, customer_id, "Schedule it at 10:45")["pending_action"]
    before = _count(agent_sessions, model)

    class Later(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2031, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr("app.services.scheduling_service.datetime", Later)
    confirmed = agent_client.post(f"/api/agent/actions/{pending['action_id']}/confirm")

    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["status"] == "failed"
    assert result["error_code"] == "schedule_in_past"
    assert _count(agent_sessions, model) == before


def test_rest_endpoints_reject_new_past_activities_but_keep_history_editable(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    customer_id = agent_records["customer_id"]
    past = PAST_AT.isoformat()
    for path, body in (
        ("/api/meetings", {"scheduled_at": past}),
        ("/api/calls", {"scheduled_at": past, "status": "scheduled"}),
        ("/api/followups", {"type": "call back", "due_date": past}),
    ):
        response = agent_client.post(path, json={"customer_id": customer_id, **body})
        assert response.status_code == 422, (path, response.text)
        assert response.json()["error"]["code"] == "schedule_in_past"

    # An overdue follow-up is past-due by definition and stays allowed.
    overdue = agent_client.post(
        "/api/followups",
        json={"customer_id": customer_id, "type": "late", "due_date": past, "status": "overdue"},
    )
    assert overdue.status_code == 201, overdue.text

    # A meeting already in the past stays editable; moving it to another past
    # time does not.
    with agent_sessions() as db:
        old = Meeting(
            customer_id=customer_id,
            scheduled_at=datetime(2026, 6, 1, 9, 0),
            duration=30,
            status="scheduled",
        )
        db.add(old)
        db.commit()
        meeting_path = f"/api/meetings/{old.meeting_id}"
    assert agent_client.put(meeting_path, json={"notes": "Recap sent"}).status_code == 200
    moved = agent_client.put(meeting_path, json={"scheduled_at": past})
    assert moved.status_code == 422
    assert moved.json()["error"]["code"] == "schedule_in_past"


def test_chat_schedule_task_lifecycle_after_submission_expiry_and_cancellation(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_id = agent_records["customer_id"]
    _chat_model(
        monkeypatch,
        _tool_call(
            "create_meeting",
            {"customer_id": customer_id, **_schedule_args("create_meeting", PAST_AT)},
        ),
        AIMessage(content="That time has already passed."),
    )
    response = _post_chat(agent_client, customer_id, "Schedule it on August 7 at 10:45")
    task_id = response["clarification_task"]["task_id"]
    draft = _schedule_args("create_meeting", PAST_AT) | {"customer_id": customer_id}

    def submit(target: str, values: dict[str, Any]) -> Any:
        return agent_client.post(
            f"/api/agent/tasks/{target}/inputs",
            json={"operation_index": 0, "values": values},
        )

    slots = submit(task_id, {}).json()["error"]["details"]["suggestions"]
    assert submit(task_id, _suggestion_values("create_meeting", slots[0])).status_code == 200
    proposals = _count(agent_sessions, HitlActionProposal)

    # The reported bug: a stale "Schedule a new meeting" click on a submitted task.
    stale = submit(task_id, {})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "invalid_task_transition"
    # The chat now reads the task first and finds the proposal it can show instead.
    current = agent_client.get(f"/api/agent/tasks/{task_id}").json()
    assert current["status"] == "awaiting_approval"
    assert current["operations"][0]["action_id"]
    assert _count(agent_sessions, HitlActionProposal) == proposals

    # An expired task rejects input; the chat replaces it rather than retrying it.
    tasks = _count(agent_sessions, HitlTask)
    expired_id = _post_chat(agent_client, customer_id, "Schedule it on August 7 at 10:45")[
        "clarification_task"
    ]["task_id"]
    with agent_sessions() as db:
        task = db.get(HitlTask, expired_id)
        assert task is not None
        task.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.commit()
    expired = submit(expired_id, {})
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "task_expired"

    # A replacement task built from the original request returns checked future
    # times without persisting anything, then creates exactly one approval proposal.
    assert agent_client.post(f"/api/agent/tasks/{task_id}/cancel").status_code == 200
    replaced_tasks = _count(agent_sessions, HitlTask)
    fresh = agent_client.post(
        "/api/agent/tasks",
        json={"operations": [{"action": "create_meeting", "values": draft}]},
    )
    assert fresh.status_code == 422
    assert fresh.json()["error"]["code"] == "schedule_in_past"
    fresh_slots = fresh.json()["error"]["details"]["suggestions"]
    assert fresh_slots
    assert _count(agent_sessions, HitlTask) == replaced_tasks

    created = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": "create_meeting",
                    "values": draft | _suggestion_values("create_meeting", fresh_slots[0]),
                }
            ]
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "awaiting_approval"
    assert _count(agent_sessions, HitlTask) == replaced_tasks + 1
    assert tasks + 1 == replaced_tasks


def _scheduling_with_lookups(customer_id: int) -> list[AIMessage]:
    # A realistic Gemini path: four read rounds, then the scheduling tool.
    reads = (
        "get_customer_profile",
        "get_customer_meetings",
        "get_customer_calls",
        "get_customer_followups",
    )
    return [
        *(_tool_call(name, {"customer_id": customer_id}, f"read-{name}") for name in reads),
        _tool_call(
            "create_meeting",
            {
                "customer_id": customer_id,
                "scheduled_date": "2030-10-08",
                "scheduled_time": "15:00",
            },
            "propose",
        ),
    ]


def test_scheduling_with_lookups_terminates_within_default_step_limit(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    customer_id = agent_records["customer_id"]
    # The old configured limit of 8 cannot fit five tool rounds (2 graph steps each).
    monkeypatch.setattr(settings, "agent_recursion_limit", 8)
    _chat_model(monkeypatch, *_scheduling_with_lookups(customer_id))
    failed = agent_client.post(
        "/api/agent/chat",
        json={"message": "Schedule a meeting at 15:00", "customer_id": customer_id},
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "agent_step_limit"

    monkeypatch.setattr(
        settings,
        "agent_recursion_limit",
        Settings.model_fields["agent_recursion_limit"].default,
    )
    _chat_model(monkeypatch, *_scheduling_with_lookups(customer_id))
    proposed = _post_chat(agent_client, customer_id, "Schedule a meeting at 15:00")
    assert proposed["pending_action"]["action"] == "create_meeting"
    assert _count(agent_sessions, HitlActionProposal) == 1


def test_tool_call_loop_fails_safely_without_crm_effects(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.actions import action_checkpointer

    customer_id = agent_records["customer_id"]
    monkeypatch.setattr(settings, "agent_recursion_limit", 12)
    # The fake model repeats its only response, so it requests the same tool forever.
    _chat_model(
        monkeypatch,
        _tool_call("get_customer_profile", {"customer_id": customer_id}, "loop"),
    )
    before = {
        model: _count(agent_sessions, model)
        for model in (Meeting, HitlActionProposal, HitlTask, HitlExecutionAudit)
    }
    threads_before = set(action_checkpointer.storage)

    response = agent_client.post(
        "/api/agent/chat",
        json={"message": "Schedule a meeting at 15:00", "customer_id": customer_id},
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "agent_step_limit"
    assert "No CRM changes were made" in error["message"]
    assert "saved" not in error["message"]
    assert {model: _count(agent_sessions, model) for model in before} == before
    assert set(action_checkpointer.storage) == threads_before


def test_task_input_submission_never_reruns_the_agent_graph(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_recursion_limit", 12)
    meetings_before = _count(agent_sessions, Meeting)
    task = _post_meeting_clarification(
        agent_client,
        monkeypatch,
        agent_records["customer_id"],
        {"agenda": "Discuss the latest enquiry."},
    )["clarification_task"]

    def _graph_must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("task input submission must not invoke the agent graph")

    monkeypatch.setattr(agent_action_service, "build_sales_agent", _graph_must_not_run)
    values = {"scheduled_date": "2030-10-12", "scheduled_time": "15:00"}
    submitted = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={"operation_index": 0, "values": values},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "awaiting_approval"

    repeated = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={"operation_index": 0, "values": values},
    )
    assert repeated.status_code == 409
    assert _count(agent_sessions, HitlActionProposal) == 1
    assert _count(agent_sessions, Meeting) == meetings_before


def test_choosing_suggested_meeting_time_saves_proposal_with_foreign_keys_enforced(
    agent_client: TestClient,
    agent_engine: Any,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQLite ignores FKs by default; MySQL enforces them (error 1452 in production).
    with agent_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(settings, "agent_recursion_limit", 12)
    customer_id = agent_records["customer_id"]
    busy = datetime(2030, 10, 14, 16, 0, tzinfo=timezone.utc)
    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=customer_id,
                scheduled_at=busy.replace(tzinfo=None),
                duration=60,
                status="scheduled",
            )
        )
        db.commit()
    _chat_model(
        monkeypatch,
        _tool_call("create_meeting", {"customer_id": customer_id, **_schedule_args("create_meeting", busy)}),
        AIMessage(content="That time conflicts with a meeting."),
    )
    response = _post_chat(agent_client, customer_id, "Schedule a meeting at 16:00")
    task = response["clarification_task"]
    choice = response["schedule_alternatives"][0]
    meetings_before = _count(agent_sessions, Meeting)

    chosen = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={"operation_index": 0, "values": _suggestion_values("create_meeting", choice)},
    )

    assert chosen.status_code == 200, chosen.text
    operation = chosen.json()["operations"][0]
    assert chosen.json()["status"] == "awaiting_approval"
    with agent_sessions() as db:
        assert db.query(HitlActionProposal).filter_by(task_id=task["task_id"]).count() == 1
        assert db.scalar(
            select(HitlActionAudit).where(HitlActionAudit.action_id == operation["action_id"])
        ).event_type == "proposed"
    assert _count(agent_sessions, Meeting) == meetings_before

    approved = agent_client.post(f"/api/agent/actions/{operation['action_id']}/confirm")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert _count(agent_sessions, Meeting) == meetings_before + 1
