"""Preserve the timezone selected for each meeting.

Revision ID: 20261009_0008
Revises: 20261009_0007
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0008"
down_revision: Union[str, None] = "20261009_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "scheduled_timezone",
            sa.String(length=64),
            nullable=False,
            server_default="UTC",
        ),
    )


def downgrade() -> None:
    """Drop saved timezone labels; they cannot be reconstructed after downgrade."""
    op.drop_column("meetings", "scheduled_timezone")
