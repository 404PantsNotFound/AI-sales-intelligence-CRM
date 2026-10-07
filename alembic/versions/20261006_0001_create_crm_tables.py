"""Create initial CRM tables.

Revision ID: 20261006_0001
Revises:
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261006_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("company_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=150)),
        sa.Column("website", sa.String(length=255)),
        sa.Column("address", sa.String(length=255)),
        sa.Column("city", sa.String(length=120)),
        sa.Column("country", sa.String(length=120)),
        sa.Column("company_size", sa.String(length=100)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_companies_company_name", "companies", ["company_name"])

    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("sales_stage", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.company_id"], name="fk_customers_company_id_companies", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_customers_company_id", "customers", ["company_id"])
    op.create_index("ix_customers_customer_name", "customers", ["customer_name"])
    op.create_index("ix_customers_company_customer_name", "customers", ["company_id", "customer_name"])

    op.create_table(
        "contacts",
        sa.Column("contact_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("job_title", sa.String(length=150)),
        sa.Column("email", sa.String(length=320)),
        sa.Column("phone", sa.String(length=50)),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], name="fk_contacts_customer_id_customers", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_contacts_customer_id", "contacts", ["customer_id"])
    op.create_index("ix_contacts_email", "contacts", ["email"])
    op.create_index("ix_contacts_customer_id_name", "contacts", ["customer_id", "name"])

    op.create_table(
        "sales_enquiries",
        sa.Column("enquiry_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product", sa.String(length=255)),
        sa.Column("enquiry_text", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("estimated_value", sa.Numeric(precision=12, scale=2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.customer_id"],
            name="fk_sales_enquiries_customer_id_customers",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_sales_enquiries_customer_id", "sales_enquiries", ["customer_id"])
    op.create_index("ix_sales_enquiries_status", "sales_enquiries", ["status"])
    op.create_index("ix_sales_enquiries_priority", "sales_enquiries", ["priority"])
    op.create_index("ix_sales_enquiries_created_at", "sales_enquiries", ["created_at"])
    op.create_index(
        "ix_sales_enquiries_customer_created_at", "sales_enquiries", ["customer_id", "created_at"]
    )

    op.create_table(
        "meetings",
        sa.Column("meeting_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer()),
        sa.Column("enquiry_id", sa.Integer()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration", sa.Integer()),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("agenda", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], name="fk_meetings_customer_id_customers", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.contact_id"], name="fk_meetings_contact_id_contacts", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["enquiry_id"],
            ["sales_enquiries.enquiry_id"],
            name="fk_meetings_enquiry_id_sales_enquiries",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_meetings_customer_id", "meetings", ["customer_id"])
    op.create_index("ix_meetings_contact_id", "meetings", ["contact_id"])
    op.create_index("ix_meetings_enquiry_id", "meetings", ["enquiry_id"])
    op.create_index("ix_meetings_scheduled_at", "meetings", ["scheduled_at"])
    op.create_index("ix_meetings_status", "meetings", ["status"])
    op.create_index("ix_meetings_customer_scheduled_at", "meetings", ["customer_id", "scheduled_at"])

    op.create_table(
        "calls",
        sa.Column("call_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer()),
        sa.Column("enquiry_id", sa.Integer()),
        sa.Column("call_type", sa.String(length=50)),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("actual_time", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("outcome", sa.String(length=100)),
        sa.Column("notes", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("next_followup_date", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], name="fk_calls_customer_id_customers", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.contact_id"], name="fk_calls_contact_id_contacts", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["enquiry_id"],
            ["sales_enquiries.enquiry_id"],
            name="fk_calls_enquiry_id_sales_enquiries",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_calls_customer_id", "calls", ["customer_id"])
    op.create_index("ix_calls_contact_id", "calls", ["contact_id"])
    op.create_index("ix_calls_enquiry_id", "calls", ["enquiry_id"])
    op.create_index("ix_calls_scheduled_at", "calls", ["scheduled_at"])
    op.create_index("ix_calls_actual_time", "calls", ["actual_time"])
    op.create_index("ix_calls_status", "calls", ["status"])
    op.create_index("ix_calls_next_followup_date", "calls", ["next_followup_date"])
    op.create_index("ix_calls_customer_scheduled_at", "calls", ["customer_id", "scheduled_at"])

    op.create_table(
        "follow_ups",
        sa.Column("followup_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("enquiry_id", sa.Integer()),
        sa.Column("meeting_id", sa.Integer()),
        sa.Column("call_id", sa.Integer()),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("assigned_to", sa.String(length=255)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.customer_id"], name="fk_follow_ups_customer_id_customers", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["enquiry_id"],
            ["sales_enquiries.enquiry_id"],
            name="fk_follow_ups_enquiry_id_sales_enquiries",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"], ["meetings.meeting_id"], name="fk_follow_ups_meeting_id_meetings", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["call_id"], ["calls.call_id"], name="fk_follow_ups_call_id_calls", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_follow_ups_customer_id", "follow_ups", ["customer_id"])
    op.create_index("ix_follow_ups_enquiry_id", "follow_ups", ["enquiry_id"])
    op.create_index("ix_follow_ups_meeting_id", "follow_ups", ["meeting_id"])
    op.create_index("ix_follow_ups_call_id", "follow_ups", ["call_id"])
    op.create_index("ix_follow_ups_due_date", "follow_ups", ["due_date"])
    op.create_index("ix_follow_ups_status", "follow_ups", ["status"])
    op.create_index("ix_follow_ups_customer_due_date", "follow_ups", ["customer_id", "due_date"])


def downgrade() -> None:
    op.drop_table("follow_ups")
    op.drop_table("calls")
    op.drop_table("meetings")
    op.drop_table("sales_enquiries")
    op.drop_table("contacts")
    op.drop_table("customers")
    op.drop_table("companies")
