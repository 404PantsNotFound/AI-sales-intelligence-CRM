from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.tools import build_read_only_tools
from app.core.config import settings
from app.models import Call, FollowUp, Meeting
from app.schemas.agent import AgentChatRequest
from app.services.agent_action_state import pending_actions


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
        json={"message": message, "customer_id": customer_id},
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
                "scheduled_at": "2026-10-08T15:00:00+04:00",
                "duration": 60,
                "agenda": "Discuss proposal",
            },
        ),
    )
    return _post_chat(client, customer_id, "Schedule a meeting")


def _count(sessions: sessionmaker[Session], model: type[Any]) -> int:
    with sessions() as db:
        return db.query(model).count()


def test_default_agent_tools_are_read_only(
    agent_sessions: sessionmaker[Session],
) -> None:
    with agent_sessions() as db:
        names = {tool.name for tool in build_read_only_tools(db)}
    assert names.isdisjoint(
        {"create_meeting", "create_followup", "record_call_result", "complete_followup"}
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
    record = pending_actions._pending[action_id]
    pending_actions._pending[action_id] = replace(
        record,
        expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )

    response = agent_client.post(f"/api/agent/actions/{action_id}/confirm")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "action_expired"
    assert _count(agent_sessions, Meeting) == before


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
                "due_date": "2026-10-09T10:00:00+04:00",
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
                "scheduled_at": "2026-10-08T15:00:00+04:00",
            },
        ),
        AIMessage(content="I could not verify the requested contact in CRM records."),
    )
    response = _post_chat(
        agent_client,
        agent_records["customer_id"],
        "Schedule a meeting with an unverified contact.",
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
