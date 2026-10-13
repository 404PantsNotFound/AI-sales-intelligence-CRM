"""Store per-user Google Calendar connections and CRM activity event links.

Revision ID: 20261012_0010
Revises: 20261009_0009
Create Date: 2026-10-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261012_0010"
down_revision: Union[str, None] = "20261009_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "google_calendar_connections",
        sa.Column("user_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reminder_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('connected', 'reauthorization_required')",
            name="ck_google_calendar_connections_status",
        ),
        sa.CheckConstraint(
            "reminder_minutes BETWEEN 0 AND 1440",
            name="ck_google_calendar_connections_reminder_minutes",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
    )

    op.create_table(
        "calendar_event_links",
        sa.Column("link_id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("activity_type", sa.String(length=16), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("google_event_id", sa.String(length=64), nullable=False),
        sa.Column("sync_status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "activity_type IN ('meeting', 'call', 'followup')",
            name="ck_calendar_event_links_activity_type",
        ),
        sa.CheckConstraint(
            "sync_status IN ('synced', 'failed', 'deleted')",
            name="ck_calendar_event_links_sync_status",
        ),
        sa.UniqueConstraint(
            "user_id",
            "activity_type",
            "activity_id",
            name="uq_calendar_event_links_user_activity",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_calendar_event_links_user_id", "calendar_event_links", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_calendar_event_links_user_id", table_name="calendar_event_links")
    op.drop_table("calendar_event_links")
    op.drop_table("google_calendar_connections")
