"""Bind HITL approvals to immutable action payloads.

Revision ID: 20261009_0006
Revises: 20261009_0005
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261009_0006"
down_revision: Union[str, None] = "20261009_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
        "hitl_action_proposals",
        sa.Column(
            "payload_fingerprint",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.alter_column(
        "hitl_action_proposals",
        "payload_fingerprint",
        existing_type=sa.String(length=64),
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("hitl_action_proposals", "payload_fingerprint")
