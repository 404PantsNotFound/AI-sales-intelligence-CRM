"""Support imported enquiry priorities and missed calls.

Revision ID: 20261009_0004
Revises: 20261006_0003
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261009_0004"
down_revision: Union[str, None] = "20261006_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_sales_enquiries_priority",
        "sales_enquiries",
        "priority IN ('low', 'medium', 'normal', 'high', 'urgent')",
    )
    op.create_check_constraint(
        "ck_sales_enquiries_status",
        "sales_enquiries",
        "status IN ('open', 'in_progress', 'converted', 'closed', 'lost')",
    )
    op.create_check_constraint(
        "ck_calls_status",
        "calls",
        "status IN ('scheduled', 'attempted', 'completed', 'failed', 'cancelled', 'missed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_calls_status", "calls", type_="check")
    op.drop_constraint("ck_sales_enquiries_status", "sales_enquiries", type_="check")
    op.drop_constraint("ck_sales_enquiries_priority", "sales_enquiries", type_="check")
