"""Add uniqueness constraints for companies.company_name and customers(company_id, customer_name).

Revision ID: 20261006_0003
Revises: 20261006_0002
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261006_0003"
down_revision: Union[str, None] = "20261006_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_no_duplicate_records() -> None:
    bind = op.get_bind()
    duplicate_companies = bind.execute(
        sa.text(
            "SELECT LOWER(TRIM(company_name)) AS norm_name, COUNT(*) AS cnt "
            "FROM companies GROUP BY norm_name HAVING cnt > 1"
        )
    ).fetchall()
    if duplicate_companies:
        names = ", ".join(f"{row[0]} ({row[1]})" for row in duplicate_companies)
        raise RuntimeError(
            f"Cannot add uq_companies_company_name because duplicate company names exist: {names}. "
            "Resolve duplicates manually before running this migration."
        )

    duplicate_customers = bind.execute(
        sa.text(
            "SELECT company_id, LOWER(TRIM(customer_name)) AS norm_name, COUNT(*) AS cnt "
            "FROM customers GROUP BY company_id, norm_name HAVING cnt > 1"
        )
    ).fetchall()
    if duplicate_customers:
        items = ", ".join(
            f"company_id={row[0]}:{row[1]} ({row[2]})" for row in duplicate_customers
        )
        raise RuntimeError(
            "Cannot add uq_customers_company_customer_name because duplicate customer names "
            f"exist under the same company: {items}. Resolve duplicates manually before running this migration."
        )


def upgrade() -> None:
    _ensure_no_duplicate_records()
    op.create_unique_constraint(
        "uq_companies_company_name",
        "companies",
        ["company_name"],
    )
    op.create_unique_constraint(
        "uq_customers_company_customer_name",
        "customers",
        ["company_id", "customer_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_customers_company_customer_name",
        "customers",
        type_="unique",
    )
    op.drop_constraint(
        "uq_companies_company_name",
        "companies",
        type_="unique",
    )
