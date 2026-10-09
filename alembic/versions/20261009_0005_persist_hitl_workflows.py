"""Persist HITL tasks, decisions, execution audits, and policies.

Revision ID: 20261009_0005
Revises: 20261009_0004
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0005"
down_revision: Union[str, None] = "20261009_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hitl_tasks",
        sa.Column("task_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'executing', 'completed', "
            "'rejected', 'cancelled', 'failed', 'expired')",
            name="ck_hitl_tasks_status",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_hitl_tasks_owner_user_id", "hitl_tasks", ["owner_user_id"])
    op.create_index(
        "ix_hitl_tasks_owner_status_expiry",
        "hitl_tasks",
        ["owner_user_id", "status", "expires_at"],
    )

    op.create_table(
        "hitl_action_proposals",
        sa.Column("action_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'completed', 'rejected', "
            "'cancelled', 'failed', 'expired')",
            name="ck_hitl_action_proposals_status",
        ),
        sa.CheckConstraint(
            "action_type IN ('create_meeting', 'create_followup', "
            "'record_call_result', 'complete_followup', 'apply_enrichment')",
            name="ck_hitl_action_proposals_action_type",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["hitl_tasks.task_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_hitl_action_proposals_action_type",
        "hitl_action_proposals",
        ["action_type"],
    )
    op.create_index(
        "ix_hitl_action_proposals_task_status",
        "hitl_action_proposals",
        ["task_id", "status"],
    )
    op.create_index(
        "ix_hitl_action_proposals_owner_status_expiry",
        "hitl_action_proposals",
        ["owner_user_id", "status", "expires_at"],
    )

    op.create_table(
        "hitl_approval_decisions",
        sa.Column("decision_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_hitl_approval_decisions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["hitl_action_proposals.action_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("action_id", name="uq_hitl_approval_decisions_action_id"),
    )
    op.create_index(
        "ix_hitl_approval_decisions_decided_at",
        "hitl_approval_decisions",
        ["decided_at"],
    )

    op.create_table(
        "hitl_execution_audits",
        sa.Column("execution_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("initiated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_record_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="ck_hitl_execution_audits_status",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["hitl_action_proposals.action_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("action_id", name="uq_hitl_execution_audits_action_id"),
    )
    op.create_index(
        "ix_hitl_execution_audits_status_started",
        "hitl_execution_audits",
        ["status", "started_at"],
    )

    op.create_table(
        "hitl_policy_overrides",
        sa.Column("action_type", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "mode IN ('automatic', 'approval_required', 'disabled')",
            name="ck_hitl_policy_overrides_mode",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "hitl_policy_audits",
        sa.Column("audit_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("previous_mode", sa.String(length=32), nullable=False),
        sa.Column("new_mode", sa.String(length=32), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "previous_mode IN ('automatic', 'approval_required', 'disabled')",
            name="ck_hitl_policy_audits_previous_mode",
        ),
        sa.CheckConstraint(
            "new_mode IN ('automatic', 'approval_required', 'disabled')",
            name="ck_hitl_policy_audits_new_mode",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_hitl_policy_audits_action_changed",
        "hitl_policy_audits",
        ["action_type", "changed_at"],
    )

    op.create_table(
        "hitl_action_audits",
        sa.Column("audit_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('proposed', 'approved', 'rejected', 'expired', "
            "'execution_started', 'execution_completed', 'execution_failed')",
            name="ck_hitl_action_audits_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["hitl_action_proposals.action_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.user_id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_hitl_action_audits_action_created",
        "hitl_action_audits",
        ["action_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hitl_action_audits_action_created", table_name="hitl_action_audits")
    op.drop_table("hitl_action_audits")
    op.drop_index("ix_hitl_policy_audits_action_changed", table_name="hitl_policy_audits")
    op.drop_table("hitl_policy_audits")
    op.drop_table("hitl_policy_overrides")
    op.drop_index(
        "ix_hitl_execution_audits_status_started",
        table_name="hitl_execution_audits",
    )
    op.drop_table("hitl_execution_audits")
    op.drop_index(
        "ix_hitl_approval_decisions_decided_at",
        table_name="hitl_approval_decisions",
    )
    op.drop_table("hitl_approval_decisions")
    op.drop_index(
        "ix_hitl_action_proposals_owner_status_expiry",
        table_name="hitl_action_proposals",
    )
    op.drop_index(
        "ix_hitl_action_proposals_task_status",
        table_name="hitl_action_proposals",
    )
    op.drop_index(
        "ix_hitl_action_proposals_action_type",
        table_name="hitl_action_proposals",
    )
    op.drop_table("hitl_action_proposals")
    op.drop_index("ix_hitl_tasks_owner_status_expiry", table_name="hitl_tasks")
    op.drop_index("ix_hitl_tasks_owner_user_id", table_name="hitl_tasks")
    op.drop_table("hitl_tasks")
