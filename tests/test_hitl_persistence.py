from pathlib import Path
from datetime import datetime, timezone
import runpy
import sqlite3

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.exceptions import APIError
from app.database.connection import Base
from app.models import (
    HitlActionAudit,
    HitlActionProposal,
    HitlApprovalDecision,
    HitlExecutionAudit,
    HitlPolicyAudit,
    HitlPolicyOverride,
    HitlTask,
    User,
)
from app.services.agent_action_state import PendingActionStore
from app.services.hitl_policy import (
    DEFAULT_POLICIES,
    get_action_policy,
    set_action_policy,
)


def _create_user(db: Session, email: str, role: str = "sales") -> User:
    user = User(
        email=email,
        full_name="HITL Test User",
        password_hash="test-only-hash",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def test_pending_action_and_execution_audit_survive_sessions(
    agent_sessions: sessionmaker[Session],
) -> None:
    store = PendingActionStore()
    with agent_sessions() as db:
        user = _create_user(db, "hitl.persist@example.com")
        record = store.add(
            db,
            "create_meeting",
            {"customer_id": 21, "agenda": "Quarterly review"},
            "not-persisted-graph-thread",
            user_id=user.user_id,
        )
        owner_id = user.user_id

    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, record.action_id)
        assert proposal is not None
        assert proposal.parameters == {
            "customer_id": 21,
            "agenda": "Quarterly review",
        }
        task = db.get(HitlTask, record.task_id)
        assert task is not None
        assert task.owner_user_id == owner_id
        assert task.status == "awaiting_approval"
        assert db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == record.action_id,
                HitlActionAudit.event_type == "proposed",
            )
        ) is not None

    with agent_sessions() as db:
        other_user = _create_user(db, "hitl.other@example.com")
        with pytest.raises(APIError) as exc_info:
            store.claim(db, record.action_id, user_id=other_user.user_id)
        assert exc_info.value.status_code == 403

    with agent_sessions() as db:
        claimed = store.claim(db, record.action_id, user_id=owner_id)
        assert claimed.action_id == record.action_id

    with agent_sessions() as db:
        with pytest.raises(APIError) as exc_info:
            store.claim(db, record.action_id, user_id=owner_id)
        assert exc_info.value.status_code == 409

    with agent_sessions() as db:
        decision = db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == record.action_id
            )
        )
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == record.action_id
            )
        )
        assert decision is not None
        assert decision.decision == "approved"
        assert decision.decided_by_user_id == owner_id
        assert execution is not None
        assert execution.status == "started"

    with agent_sessions() as db:
        store.finish(
            db,
            claimed,
            status="completed",
            result_record_id=912,
            error_code=None,
            result_message="Meeting created successfully.",
        )
    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, record.action_id)
        task = db.get(HitlTask, record.task_id)
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == record.action_id
            )
        )
        completed_event = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == record.action_id,
                HitlActionAudit.event_type == "execution_completed",
            )
        )
        assert proposal is not None and proposal.status == "completed"
        assert task is not None and task.status == "completed"
        assert execution is not None
        assert execution.status == "completed"
        assert execution.result_record_id == 912
        assert completed_event is not None
        assert completed_event.details == {
            "status": "completed",
            "record_id": 912,
            "error_code": None,
            "message": "Meeting created successfully.",
        }


def test_pending_action_add_persists_parents_before_audits_with_foreign_keys() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    insert_order: list[str] = []

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "before_cursor_execute")
    def record_insert_order(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO"):
            insert_order.append(statement.split()[2].strip('"`'))

    try:
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        with sessions() as db:
            user = _create_user(db, "hitl.fk-order@example.com")
            insert_order.clear()

            record = PendingActionStore().add(
                db,
                "create_meeting",
                {"customer_id": 21, "agenda": "Quarterly review"},
                "unused-thread",
                user_id=user.user_id,
            )

            assert insert_order == [
                "hitl_tasks",
                "hitl_action_proposals",
                "hitl_action_audits",
            ]
            proposal = db.get(HitlActionProposal, record.action_id)
            assert proposal is not None
            assert proposal.status == "pending"
            task = db.get(HitlTask, record.task_id)
            assert task is not None
            assert task.status == "awaiting_approval"
            assert db.scalar(
                select(HitlExecutionAudit).where(
                    HitlExecutionAudit.action_id == record.action_id
                )
            ) is None
            audit = db.scalar(
                select(HitlActionAudit).where(
                    HitlActionAudit.action_id == record.action_id
                )
            )
            assert audit is not None
            assert audit.event_type == "proposed"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_pending_action_persistence_failure_logs_safe_traceback(
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_payload = (
        "Confidential Customer Name; private enquiry description; "
        "database-password=do-not-log; bearer confidential-jwt"
    )
    store = PendingActionStore()
    with agent_sessions() as db:
        rollback = db.rollback
        rollback_called = False

        def fail_commit() -> None:
            raise IntegrityError(
                f"INSERT INTO hitl_action_proposals VALUES ('{sensitive_payload}')",
                {"parameters": sensitive_payload},
                Exception(
                    1062,
                    f"Duplicate entry '{sensitive_payload}' for key "
                    "'ai_sales_crm.hitl_action_proposals.PRIMARY'",
                ),
            )

        def record_rollback() -> None:
            nonlocal rollback_called
            rollback_called = True
            rollback()

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", record_rollback)

        with caplog.at_level("ERROR", logger="app.services.agent_action_state"):
            with pytest.raises(APIError) as exc_info:
                store.add(
                    db,
                    "create_meeting",
                    {"customer_id": 1, "agenda": sensitive_payload},
                    "unused-thread",
                    user_id=1,
                )

    assert exc_info.value.status_code == 500
    assert exc_info.value.message == "The pending action could not be saved."
    assert rollback_called is True
    assert (
        "operation=save_pending_action error_type=IntegrityError "
        "mysql_error_code=1062 constraint_or_column=PRIMARY"
    ) in caplog.text
    assert "in add" in caplog.text
    assert sensitive_payload not in caplog.text
    assert "Confidential Customer Name" not in caplog.text
    assert "private enquiry description" not in caplog.text
    assert "do-not-log" not in caplog.text
    assert "confidential-jwt" not in caplog.text


def test_pending_action_fk_diagnostic_logs_identifiers_only(
    agent_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_payload = (
        "Confidential Customer Name; private enquiry description; "
        "database-password-value; bearer confidential-jwt"
    )
    message = (
        "Cannot add or update a child row: a foreign key constraint fails "
        "(`ai_sales_crm`.`hitl_action_audits`, "
        "CONSTRAINT `hitl_action_audits_ibfk_1` FOREIGN KEY (`action_id`) "
        "REFERENCES `ai_sales_crm`.`hitl_action_proposals` (`action_id`)) "
        f"{sensitive_payload}"
    )
    store = PendingActionStore()
    with agent_sessions() as db:
        def fail_commit() -> None:
            raise IntegrityError(
                f"INSERT INTO hitl_action_audits VALUES ('{sensitive_payload}')",
                {"parameters": sensitive_payload},
                Exception(1452, message),
            )

        monkeypatch.setattr(db, "commit", fail_commit)

        with caplog.at_level("ERROR", logger="app.services.agent_action_state"):
            with pytest.raises(APIError) as exc_info:
                store.add(
                    db,
                    "create_meeting",
                    {"customer_id": 1, "agenda": sensitive_payload},
                    "unused-thread",
                    user_id=1,
                )

    assert exc_info.value.message == "The pending action could not be saved."
    assert (
        "mysql_error_code=1452 "
        "constraint_or_column=hitl_action_audits_ibfk_1 "
        "child_table=hitl_action_audits child_column=action_id "
        "parent_table=hitl_action_proposals parent_column=action_id"
    ) in caplog.text
    assert "foreign key constraint fails" not in caplog.text
    assert sensitive_payload not in caplog.text
    assert "INSERT INTO hitl_action_audits" not in caplog.text
    assert "parameters" not in caplog.text
    assert "Confidential Customer Name" not in caplog.text
    assert "private enquiry description" not in caplog.text
    assert "database-password-value" not in caplog.text
    assert "confidential-jwt" not in caplog.text


def test_policy_override_and_change_audit_persist(
    agent_sessions: sessionmaker[Session],
) -> None:
    action = "create_meeting"
    with agent_sessions() as db:
        user = _create_user(db, "hitl.policy-admin@example.com", role="admin")
        email = user.email
        assert get_action_policy(db, action) == DEFAULT_POLICIES[action]

    with agent_sessions() as db:
        result = set_action_policy(
            db,
            action,
            "disabled",
            changed_by=email,
        )
        assert result["previous_mode"] == "approval_required"
        assert result["new_mode"] == "disabled"

    with agent_sessions() as db:
        assert get_action_policy(db, action) == "disabled"
        override = db.get(HitlPolicyOverride, action)
        assert override is not None
        assert override.updated_by_user_id is not None

        audit = db.scalar(
            select(HitlPolicyAudit).where(
                HitlPolicyAudit.action_type == action
            )
        )
        assert audit is not None
        assert audit.previous_mode == "approval_required"
        assert audit.new_mode == "disabled"
        assert audit.changed_by_user_id == override.updated_by_user_id


def test_policy_defaults_remain_safe_without_override(
    agent_sessions: sessionmaker[Session],
) -> None:
    with agent_sessions() as db:
        assert get_action_policy(db, "apply_enrichment") == "approval_required"
        assert db.get(HitlPolicyOverride, "apply_enrichment") is None


def test_rejection_decision_and_state_are_persisted(
    agent_sessions: sessionmaker[Session],
) -> None:
    store = PendingActionStore()
    with agent_sessions() as db:
        user = _create_user(db, "hitl.reject@example.com")
        record = store.add(
            db,
            "create_followup",
            {"customer_id": 4, "type": "email"},
            "unused-thread",
            user_id=user.user_id,
        )

    with agent_sessions() as db:
        store.reject(db, record.action_id, user_id=user.user_id)

    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, record.action_id)
        task = db.get(HitlTask, record.task_id)
        decision = db.scalar(
            select(HitlApprovalDecision).where(
                HitlApprovalDecision.action_id == record.action_id
            )
        )
        audit = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == record.action_id,
                HitlActionAudit.event_type == "rejected",
            )
        )
        assert proposal is not None and proposal.status == "rejected"
        assert task is not None and task.status == "rejected"
        assert decision is not None
        assert decision.decision == "rejected"
        assert audit is not None


def test_stale_execution_is_failed_without_automatic_replay(
    agent_sessions: sessionmaker[Session],
) -> None:
    store = PendingActionStore()
    with agent_sessions() as db:
        user = _create_user(db, "hitl.stale-execution@example.com")
        started = store.add(
            db,
            "create_meeting",
            {"customer_id": 19},
            "unused",
            user_id=user.user_id,
        )
        store.claim(db, started.action_id, user_id=user.user_id)
        proposal = db.get(HitlActionProposal, started.action_id)
        assert proposal is not None
        proposal.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()

    with agent_sessions() as db:
        store.add(
            db,
            "create_followup",
            {"customer_id": 19},
            "unused",
            user_id=user.user_id,
        )

    with agent_sessions() as db:
        proposal = db.get(HitlActionProposal, started.action_id)
        task = db.get(HitlTask, started.task_id)
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == started.action_id
            )
        )
        audit = db.scalar(
            select(HitlActionAudit).where(
                HitlActionAudit.action_id == started.action_id,
                HitlActionAudit.event_type == "execution_failed",
            )
        )
        assert proposal is not None and proposal.status == "failed"
        assert task is not None and task.status == "failed"
        assert execution is not None
        assert execution.error_code == "execution_interrupted"
        assert audit is not None
        assert audit.details == {
            "error_code": "execution_interrupted",
            "requires_reconciliation": True,
        }


def test_hitl_models_and_migration_follow_current_head() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261009_0005_persist_hitl_workflows.py"
    )
    migration = runpy.run_path(str(migration_path))
    approval_migration = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20261009_0006_bind_hitl_approval_payload.py"
        )
    )

    assert migration["revision"] == "20261009_0005"
    assert migration["down_revision"] == "20261009_0004"
    assert approval_migration["revision"] == "20261009_0006"
    assert approval_migration["down_revision"] == "20261009_0005"
    assert {
        "hitl_tasks",
        "hitl_action_proposals",
        "hitl_approval_decisions",
        "hitl_execution_audits",
        "hitl_policy_overrides",
        "hitl_policy_audits",
        "hitl_action_audits",
    }.issubset(set(HitlTask.metadata.tables))
