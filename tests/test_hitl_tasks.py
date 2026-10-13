from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token, hash_password
from app.models import (
    Call,
    Company,
    Customer,
    FollowUp,
    HitlActionAudit,
    HitlActionProposal,
    HitlApprovalDecision,
    HitlExecutionAudit,
    Meeting,
    HitlTask,
    SalesEnquiry,
    User,
)
from app.services.agent_action_state import PendingActionStore
from app.services.hitl_policy import set_action_policy
from app.services.intelligence_service import suggest_actions
from tests.conftest import legacy_organization_id


def _create_second_user(
    sessions: sessionmaker[Session],
    client: TestClient,
) -> User:
    with sessions() as db:
        user = User(
            email="another.task.owner@example.com",
            full_name="Another User",
            password_hash=hash_password("AnotherPassword123!"),
            role="sales",
            is_active=True,
            organization_id=legacy_organization_id(db),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token, _ = create_access_token(user)
    client.headers["Authorization"] = f"Bearer {token}"
    return user


def _start_meeting_task(
    client: TestClient,
    customer_id: int,
) -> dict[str, Any]:
    response = client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": "create_meeting",
                    "values": {
                        "customer_id": customer_id,
                        "scheduled_timezone": "Asia/Dubai",
                    },
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_structured_task_returns_missing_fields_and_stable_crm_choices(
    agent_client: TestClient,
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])

    assert task["status"] == "collecting_information"
    assert task["operations"][0]["missing_fields"] == [
        "scheduled_date",
        "scheduled_time",
    ]
    customer_field = next(
        field for field in task["operations"][0]["fields"] if field["name"] == "customer_id"
    )
    assert customer_field["choices"] == [
        {
            "id": agent_records["customer_id"],
            "label": f"#{agent_records['customer_id']} ABC Trading Customer",
        }
    ]


def test_registry_returns_clarifications_for_all_write_actions(
    agent_client: TestClient,
) -> None:
    expected = {
        "create_meeting": {
            "customer_id",
            "scheduled_date",
            "scheduled_time",
            "scheduled_timezone",
        },
        "create_followup": {"customer_id", "type", "due_date"},
        "record_call_result": {"customer_id", "outcome or notes or summary"},
        "complete_followup": {"customer_id", "followup_id"},
        "apply_enrichment": {"customer_id"},
    }
    for action, expected_fields in expected.items():
        response = agent_client.post(
            "/api/agent/tasks",
            json={"operations": [{"action": action, "values": {}}]},
        )
        assert response.status_code == 200, response.text
        operation = response.json()["operations"][0]
        assert expected_fields.issubset(set(operation["missing_fields"]))
        assert operation["fields"]


def test_task_input_validates_values_and_creates_approval_proposal(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    task_id = task["task_id"]

    malformed = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={"operation_index": 0, "values": {"scheduled_time": "not-a-time"}},
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_task_input"

    completed = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": "2026-10-12",
                "scheduled_time": "15:00",
            },
        },
    )
    assert completed.status_code == 200, completed.text
    data = completed.json()
    assert data["status"] == "awaiting_approval"
    operation = data["operations"][0]
    assert operation["status"] == "pending"
    assert operation["action_id"]

    with agent_sessions() as db:
        task_row = db.get(HitlTask, task_id)
        proposal = db.get(HitlActionProposal, operation["action_id"])
        assert task_row is not None
        assert task_row.collected_data["operations"][0]["action_id"] == operation["action_id"]
        assert proposal is not None and proposal.status == "pending"
        assert proposal.task_id == task_id


def test_structured_meeting_task_rejects_conflict_before_proposal_and_returns_alternatives(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    task_id = task["task_id"]
    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=agent_records["customer_id"],
                scheduled_at=datetime(2030, 1, 10, 11, 0, tzinfo=timezone.utc),
                duration=30,
                status="scheduled",
            )
        )
        db.commit()

    response = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": "2030-01-10",
                "scheduled_time": "15:00",
            },
        },
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "schedule_conflict"
    suggestions = error["details"]["suggestions"]
    assert suggestions
    assert all(item["timezone"] == "Asia/Dubai" for item in suggestions)
    with agent_sessions() as db:
        task_row = db.get(HitlTask, task_id)
        assert task_row is not None
        assert task_row.status == "collecting_information"
        assert (
            task_row.collected_data["operations"][0]["values"].get("scheduled_time")
            == "15:00"
        ), task_row.collected_data
        assert db.scalar(select(HitlActionProposal).where(HitlActionProposal.task_id == task_id)) is None

    alternative = datetime.fromisoformat(suggestions[0]["starts_at"])
    retried = agent_client.post(
        f"/api/agent/tasks/{task_id}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": alternative.date().isoformat(),
                "scheduled_time": alternative.strftime("%H:%M"),
            },
        },
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "awaiting_approval"
    assert retried.json()["operations"][0]["action_id"]


def test_task_automatic_policy_executes_through_central_boundary(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        admin = User(
            email="task-policy-admin@example.com",
            full_name="Task Policy Admin",
            password_hash=hash_password("AdminPassword123!"),
            role="admin",
            is_active=True,
            organization_id=legacy_organization_id(db),
        )
        db.add(admin)
        db.commit()
        set_action_policy(
            db,
            "create_meeting",
            "automatic",
            organization_id=admin.organization_id,
            changed_by=admin.email,
        )
    with agent_sessions() as db:
        before = db.query(Meeting).count()

    response = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": "create_meeting",
                    "values": {
                        "customer_id": agent_records["customer_id"],
                        "scheduled_date": "2026-10-12",
                        "scheduled_time": "15:00",
                        "scheduled_timezone": "Asia/Dubai",
                    },
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "awaiting_approval"
    action_id = task["operations"][0]["action_id"]
    assert task["operations"][0]["status"] == "pending"
    with agent_sessions() as db:
        assert db.query(Meeting).count() == before
        proposal = db.get(HitlActionProposal, action_id)
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == action_id
            )
        )
        assert proposal is not None and proposal.status == "pending"
        assert execution is None


def test_task_rejects_unknown_fields_and_non_id_crm_targets(
    agent_client: TestClient,
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    response = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": "2026-10-12",
                "scheduled_time": "15:00",
                "customer_id": "ABC Trading Customer",
                "calendar_script": "arbitrary",
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task_input"

    unknown_id = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": "create_meeting",
                    "values": {
                        "customer_id": 999999,
                        "scheduled_date": "2026-10-12",
                        "scheduled_time": "15:00",
                        "scheduled_timezone": "Asia/Dubai",
                    },
                }
            ]
        },
    )
    assert unknown_id.status_code in {400, 404}


def test_task_resumes_from_database_but_is_owner_scoped(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    task_id = task["task_id"]

    monkeypatch.setattr(
        "app.services.hitl_task_service.pending_actions",
        PendingActionStore(),
    )
    resumed = agent_client.get(f"/api/agent/tasks/{task_id}")
    assert resumed.status_code == 200
    assert resumed.json()["operations"][0]["missing_fields"] == [
        "scheduled_date",
        "scheduled_time",
    ]
    with agent_sessions() as db:
        assert db.get(HitlTask, task_id) is not None

    _create_second_user(agent_sessions, agent_client)
    unauthorized = agent_client.get(f"/api/agent/tasks/{task_id}")
    assert unauthorized.status_code == 404
    assert unauthorized.json()["error"]["code"] == "task_not_found"


def test_task_list_reconstructs_legacy_proposals_from_persistent_storage(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        owner = db.scalar(select(User).where(User.email == "sales.rep@example.com"))
        assert owner is not None
        proposal = PendingActionStore().add(
            db,
            "apply_enrichment",
            {
                "customer_id": agent_records["customer_id"],
                "enquiry_id": None,
                "sales_stage": "qualified",
                "customer_status": None,
                "enquiry_priority": None,
                "enquiry_status": None,
                "estimated_value": None,
            },
            "unused-graph-thread",
            user_id=owner.user_id,
        )

    response = agent_client.get("/api/agent/tasks")
    assert response.status_code == 200, response.text
    tasks = response.json()
    task = next(item for item in tasks if item["task_id"] == proposal.task_id)
    operation = task["operations"][0]
    assert operation["action"] == "apply_enrichment"
    assert operation["action_id"] == proposal.action_id
    assert operation["status"] == "pending"
    assert operation["values"]["sales_stage"] == "qualified"


def test_task_list_reconstructs_persisted_meeting_proposal(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        owner = db.scalar(select(User).where(User.email == "sales.rep@example.com"))
        assert owner is not None
        proposal = PendingActionStore().add(
            db,
            "create_meeting",
            {
                "customer_id": agent_records["customer_id"],
                "scheduled_at": "2030-01-10T15:00:00+04:00",
                "scheduled_timezone": "Asia/Dubai",
                "duration": 30,
                "status": "scheduled",
            },
            "unused-graph-thread",
            user_id=owner.user_id,
        )

    response = agent_client.get("/api/agent/tasks")
    assert response.status_code == 200, response.text
    task = next(item for item in response.json() if item["task_id"] == proposal.task_id)
    operation = task["operations"][0]
    assert operation["action"] == "create_meeting"
    assert operation["action_id"] == proposal.action_id
    assert operation["values"]["scheduled_at"] == "2030-01-10T15:00:00+04:00"
    assert operation["values"]["scheduled_timezone"] == "Asia/Dubai"


def test_incomplete_task_expires_after_inactivity(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    with agent_sessions() as db:
        task_row = db.get(HitlTask, task["task_id"])
        assert task_row is not None
        task_row.updated_at = datetime.now(timezone.utc) - timedelta(hours=25)
        task_row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

    resumed = agent_client.get(f"/api/agent/tasks/{task['task_id']}")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "expired"
    assert resumed.json()["operations"][0]["action_id"] is None


def test_incomplete_task_can_be_cancelled_with_actor_recorded(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    cancelled = agent_client.post(f"/api/agent/tasks/{task['task_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["operations"][0]["fields"] == []
    with agent_sessions() as db:
        task_row = db.get(HitlTask, task["task_id"])
        assert task_row is not None
        assert task_row.status == "cancelled"
        assert task_row.cancelled_by_user_id == task_row.owner_user_id
        assert task_row.cancelled_at is not None
    rejected = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 0,
            "values": {
                "scheduled_date": "2026-10-12",
                "scheduled_time": "15:00",
            },
        },
    )
    assert rejected.status_code == 409


def test_multi_action_task_keeps_per_action_rejection_and_cancellation(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    response = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": "create_meeting",
                    "values": {
                        "customer_id": agent_records["customer_id"],
                        "scheduled_date": "2026-10-12",
                        "scheduled_time": "15:00",
                        "scheduled_timezone": "Asia/Dubai",
                    },
                },
                {
                    "action": "create_followup",
                    "values": {
                        "customer_id": agent_records["customer_id"],
                        "type": "proposal",
                        "due_date": "2026-10-13T15:00:00Z",
                    },
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "awaiting_approval"
    assert len({item["action_id"] for item in task["operations"]}) == 2

    first_id = task["operations"][0]["action_id"]
    second_id = task["operations"][1]["action_id"]
    rejected = agent_client.post(f"/api/agent/actions/{first_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert agent_client.get(f"/api/agent/tasks/{task['task_id']}").json()["status"] == "awaiting_approval"

    cancelled = agent_client.post(f"/api/agent/tasks/{task['task_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with agent_sessions() as db:
        first = db.get(HitlActionProposal, first_id)
        second = db.get(HitlActionProposal, second_id)
        task_row = db.get(HitlTask, task["task_id"])
        first_decision = db.scalar(
            select(HitlApprovalDecision).where(HitlApprovalDecision.action_id == first_id)
        )
        second_decision = db.scalar(
            select(HitlApprovalDecision).where(HitlApprovalDecision.action_id == second_id)
        )
        first_audit = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == first_id,
                HitlActionAudit.event_type == "rejected",
            )
        )
        second_audit = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == second_id,
                HitlActionAudit.event_type == "cancelled",
            )
        )
        assert first is not None and first.status == "rejected"
        assert second is not None and second.status == "cancelled"
        assert task_row is not None and task_row.status == "cancelled"
        assert task_row.cancelled_by_user_id == task_row.owner_user_id
        assert first_decision is not None and first_decision.decision == "rejected"
        assert second_decision is not None and second_decision.decision == "cancelled"
        assert first_audit is not None and first_audit.actor_user_id is not None
        assert second_audit is not None and second_audit.actor_user_id is not None


def test_task_submission_rejects_out_of_range_operation(
    agent_client: TestClient,
    agent_records: dict[str, int],
) -> None:
    task = _start_meeting_task(agent_client, agent_records["customer_id"])
    response = agent_client.post(
        f"/api/agent/tasks/{task['task_id']}/inputs",
        json={
            "operation_index": 2,
            "values": {
                "scheduled_date": "2026-10-12",
                "scheduled_time": "15:00",
            },
        },
    )
    assert response.status_code == 422


def _crm_and_action_counts(sessions: sessionmaker[Session]) -> dict[str, int]:
    with sessions() as db:
        return {
            model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Meeting, Call, FollowUp, SalesEnquiry, HitlActionProposal, HitlExecutionAudit)
        }


@pytest.mark.parametrize("codes", [["no_recent_activity"], ["no_next_step"]])
def test_attention_suggestion_only_prepares_a_task_draft(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    codes: list[str],
) -> None:
    (suggestion,) = suggest_actions(agent_records["customer_id"], agent_records["enquiry_id"], codes)
    with agent_sessions() as db:
        admin = User(
            email="suggestion-admin@example.com",
            full_name="Suggestion Admin",
            password_hash=hash_password("AdminPassword123!"),
            role="admin",
            is_active=True,
            organization_id=legacy_organization_id(db),
        )
        db.add(admin)
        db.commit()
        # Even an automatic policy cannot execute a draft that still lacks user input.
        set_action_policy(
            db,
            suggestion.kind,
            "automatic",
            organization_id=admin.organization_id,
            changed_by=admin.email,
        )
    before = _crm_and_action_counts(agent_sessions)

    response = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": suggestion.kind,
                    "values": {**suggestion.prefill, suggestion.timezone_field: "Asia/Dubai"},
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    task = response.json()
    operation = task["operations"][0]
    assert task["status"] == "collecting_information"
    assert operation["status"] != "pending" and operation["action_id"] is None
    assert sorted(operation["missing_fields"]) == sorted(suggestion.user_must_provide)
    assert operation["values"]["enquiry_id"] == agent_records["enquiry_id"]
    assert _crm_and_action_counts(agent_sessions) == before


def test_attention_suggestion_task_rejects_another_customers_enquiry(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        company = Company(organization_id=legacy_organization_id(db), company_name="Other Co")
        db.add(company)
        db.flush()
        other = Customer(company_id=company.company_id, customer_name="Other Customer")
        db.add(other)
        db.flush()
        foreign = SalesEnquiry(customer_id=other.customer_id, enquiry_text="Different customer.")
        db.add(foreign)
        db.commit()
        foreign_enquiry_id = foreign.enquiry_id
    (suggestion,) = suggest_actions(agent_records["customer_id"], foreign_enquiry_id, ["no_next_step"])
    before = _crm_and_action_counts(agent_sessions)

    response = agent_client.post(
        "/api/agent/tasks",
        json={
            "operations": [
                {
                    "action": suggestion.kind,
                    "values": {**suggestion.prefill, suggestion.timezone_field: "Asia/Dubai"},
                }
            ]
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "enquiry_not_found"
    assert "does not belong to this customer" in response.json()["error"]["message"]
    assert _crm_and_action_counts(agent_sessions) == before
    with agent_sessions() as db:
        assert db.scalar(select(func.count()).select_from(HitlTask)) == 0
