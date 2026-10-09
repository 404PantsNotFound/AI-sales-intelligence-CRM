"""Add a workspace scheduling lock and explicit activity duration/timezone fields.

Revision ID: 20261009_0009
Revises: 20261009_0008
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0009"
down_revision: Union[str, None] = "20261009_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_hitl_action_proposals_action_type",
        "hitl_action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_action_proposals_action_type",
        "hitl_action_proposals",
        "action_type IN ('create_meeting', 'create_followup', 'schedule_call', "
        "'record_call_result', 'complete_followup', 'apply_enrichment')",
    )
    op.create_table(
        "scheduling_locks",
        sa.Column("lock_id", sa.Integer(), primary_key=True, nullable=False),
    )
    op.bulk_insert(
        sa.table("scheduling_locks", sa.column("lock_id", sa.Integer())),
        [{"lock_id": 1}],
    )
    op.add_column(
        "calls",
        sa.Column("scheduled_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.add_column("calls", sa.Column("duration", sa.Integer(), nullable=True))
    op.add_column(
        "follow_ups",
        sa.Column("due_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.add_column("follow_ups", sa.Column("duration", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Dropping the timezone columns loses their labels; only instants remain."""
    op.drop_column("follow_ups", "duration")
    op.drop_column("follow_ups", "due_timezone")
    op.drop_column("calls", "duration")
    op.drop_column("calls", "scheduled_timezone")
    op.drop_table("scheduling_locks")
    op.drop_constraint(
        "ck_hitl_action_proposals_action_type",
        "hitl_action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_hitl_action_proposals_action_type",
        "hitl_action_proposals",
        "action_type IN ('create_meeting', 'create_followup', "
        "'record_call_result', 'complete_followup', 'apply_enrichment')",
    )
