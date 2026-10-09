from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="sales", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("company_name", name="uq_companies_company_name"),
    )

    company_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    industry: Mapped[str | None] = mapped_column(String(150))
    website: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    company_size: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customers: Mapped[list["Customer"]] = relationship(back_populates="company")


class SchedulingLock(Base):
    __tablename__ = "scheduling_locks"

    lock_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_company_customer_name", "company_id", "customer_name"),
        UniqueConstraint(
            "company_id",
            "customer_name",
            name="uq_customers_company_customer_name",
        ),
    )


    customer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.company_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    sales_stage: Mapped[str] = mapped_column(String(50), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="customers")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="customer")
    sales_enquiries: Mapped[list["SalesEnquiry"]] = relationship(back_populates="customer")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="customer")
    calls: Mapped[list["Call"]] = relationship(back_populates="customer")
    followups: Mapped[list["FollowUp"]] = relationship(back_populates="customer")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_customer_id_name", "customer_id", "name"),)

    contact_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="contacts")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="contact")
    calls: Mapped[list["Call"]] = relationship(back_populates="contact")


class SalesEnquiry(Base):
    __tablename__ = "sales_enquiries"
    __table_args__ = (
        Index("ix_sales_enquiries_customer_created_at", "customer_id", "created_at"),
        CheckConstraint(
            "priority IN ('low', 'medium', 'normal', 'high', 'urgent')",
            name="ck_sales_enquiries_priority",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'converted', 'closed', 'lost')",
            name="ck_sales_enquiries_status",
        ),
    )

    enquiry_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product: Mapped[str | None] = mapped_column(String(255))
    enquiry_text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(50), nullable=False, default="normal", index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open", index=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="sales_enquiries")
    meetings: Mapped[list["Meeting"]] = relationship(back_populates="enquiry")
    calls: Mapped[list["Call"]] = relationship(back_populates="enquiry")
    followups: Mapped[list["FollowUp"]] = relationship(back_populates="enquiry")


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_customer_scheduled_at", "customer_id", "scheduled_at"),)

    meeting_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.contact_id", ondelete="SET NULL"), index=True
    )
    enquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_enquiries.enquiry_id", ondelete="SET NULL"), index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    scheduled_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    duration: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduled", index=True)
    agenda: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="meetings")
    contact: Mapped[Contact | None] = relationship(back_populates="meetings")
    enquiry: Mapped[SalesEnquiry | None] = relationship(back_populates="meetings")
    followups: Mapped[list["FollowUp"]] = relationship(back_populates="meeting")


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_customer_scheduled_at", "customer_id", "scheduled_at"),
        CheckConstraint(
            "status IN ('scheduled', 'attempted', 'completed', 'failed', 'cancelled', 'missed')",
            name="ck_calls_status",
        ),
    )

    call_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.contact_id", ondelete="SET NULL"), index=True
    )
    enquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_enquiries.enquiry_id", ondelete="SET NULL"), index=True
    )
    call_type: Mapped[str | None] = mapped_column(String(50))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    actual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    duration: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduled", index=True)
    outcome: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    next_followup_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="calls")
    contact: Mapped[Contact | None] = relationship(back_populates="calls")
    enquiry: Mapped[SalesEnquiry | None] = relationship(back_populates="calls")
    followups: Mapped[list["FollowUp"]] = relationship(back_populates="call")


class FollowUp(Base):
    __tablename__ = "follow_ups"
    __table_args__ = (
        Index("ix_follow_ups_customer_due_date", "customer_id", "due_date"),
    )

    followup_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    enquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_enquiries.enquiry_id", ondelete="SET NULL"), index=True
    )
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.meeting_id", ondelete="SET NULL"), index=True
    )
    call_id: Mapped[int | None] = mapped_column(
        ForeignKey("calls.call_id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    due_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    duration: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="followups")
    enquiry: Mapped[SalesEnquiry | None] = relationship(back_populates="followups")
    meeting: Mapped[Meeting | None] = relationship(back_populates="followups")
    call: Mapped[Call | None] = relationship(back_populates="followups")
