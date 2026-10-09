"""Persist HITL task clarification and distinct cancellation transitions.

Revision ID: 20261009_0007
Revises: 20261009_0006
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0007"
down_revision: Union[str, None] = "20261009_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hitl_tasks",
        sa.Column("action_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "hitl_tasks",
        sa.Column(
            "collected_data",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column(
        "hitl_tasks",
        sa.Column(
            "cancelled_by_user_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_hitl_tasks_cancelled_by_user_id_users",
        "hitl_tasks",
        "users",
        ["cancelled_by_user_id"],
        ["user_id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "hitl_tasks",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text("UPDATE hitl_tasks SET collected_data = JSON_OBJECT()")
    )
    op.alter_column(
        "hitl_tasks",
        "collected_data",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.drop_constraint("ck_hitl_tasks_status", "hitl_tasks", type_="check")
    op.create_check_constraint(
        "ck_hitl_tasks_status",
        "hitl_tasks",
        "status IN ('collecting_information', 'ready_for_review', "
        "'awaiting_approval', 'executing', 'completed', 'rejected', "
        "'cancelled', 'failed', 'expired')",
    )
    op.drop_constraint(
        "ck_hitl_approval_decisions_decision",
        "hitl_approval_decisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_approval_decisions_decision",
        "hitl_approval_decisions",
        "decision IN ('approved', 'rejected', 'cancelled')",
    )
    op.drop_constraint(
        "ck_hitl_action_audits_event_type",
        "hitl_action_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_action_audits_event_type",
        "hitl_action_audits",
        "event_type IN ('proposed', 'approved', 'rejected', 'cancelled', "
        "'expired', 'execution_started', 'execution_completed', "
        "'execution_failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_hitl_tasks_cancelled_by_user_id_users",
        "hitl_tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_hitl_action_audits_event_type",
        "hitl_action_audits",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_action_audits_event_type",
        "hitl_action_audits",
        "event_type IN ('proposed', 'approved', 'rejected', 'expired', "
        "'execution_started', 'execution_completed', 'execution_failed')",
    )
    op.drop_constraint(
        "ck_hitl_approval_decisions_decision",
        "hitl_approval_decisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_approval_decisions_decision",
        "hitl_approval_decisions",
        "decision IN ('approved', 'rejected')",
    )
    op.drop_constraint("ck_hitl_tasks_status", "hitl_tasks", type_="check")
    op.create_check_constraint(
        "ck_hitl_tasks_status",
        "hitl_tasks",
        "status IN ('awaiting_approval', 'executing', 'completed', "
        "'rejected', 'cancelled', 'failed', 'expired')",
    )
    op.drop_column("hitl_tasks", "collected_data")
    op.drop_column("hitl_tasks", "action_type")
    op.drop_column("hitl_tasks", "cancelled_at")
    op.drop_column("hitl_tasks", "cancelled_by_user_id")
