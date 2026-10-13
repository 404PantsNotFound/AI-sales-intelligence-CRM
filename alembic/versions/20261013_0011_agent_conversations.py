"""Store per-user, per-customer CRM assistant chat history.

Revision ID: 20261013_0011
Revises: 20261012_0010
Create Date: 2026-10-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0011"
down_revision: Union[str, None] = "20261012_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("conversation_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_agent_conversations_owner_customer",
        "agent_conversations",
        ["owner_user_id", "customer_id", "updated_at"],
    )

    op.create_table(
        "agent_conversation_messages",
        sa.Column("message_id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_agent_conversation_messages_role",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.conversation_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_agent_conversation_messages_conversation",
        "agent_conversation_messages",
        ["conversation_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_conversation_messages_conversation",
        table_name="agent_conversation_messages",
    )
    op.drop_table("agent_conversation_messages")
    op.drop_index("ix_agent_conversations_owner_customer", table_name="agent_conversations")
    op.drop_table("agent_conversations")
