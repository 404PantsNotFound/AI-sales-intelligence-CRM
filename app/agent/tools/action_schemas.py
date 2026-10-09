from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from decimal import Decimal

from app.schemas.follow_up import FollowUpStatus
from app.schemas.meeting import MeetingStatus
from app.schemas.sales_enquiry import EnquiryPriority
from app.schemas.validators import MAX_TEXT_LENGTH, normalize_utc_datetime


class ActionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CreateMeetingProposal(ActionInput):
    customer_id: int = Field(ge=1)
    scheduled_at: datetime
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    duration: int | None = Field(default=60, gt=0, le=1440)
    status: MeetingStatus = "scheduled"
    agenda: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def ensure_utc_scheduled_at(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized


class CreateFollowupProposal(ActionInput):
    customer_id: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=50)
    due_date: datetime
    enquiry_id: int | None = Field(default=None, ge=1)
    meeting_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)
    status: FollowUpStatus = "pending"
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    assigned_to: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None

    @field_validator("due_date", mode="after")
    @classmethod
    def ensure_utc_due_date(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

    @field_validator("completed_at", mode="after")
    @classmethod
    def ensure_utc_completed_at(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)


class RecordCallResultProposal(ActionInput):
    customer_id: int = Field(ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    call_type: str | None = Field(default=None, max_length=50)
    actual_time: datetime | None = None
    status: Literal["completed"] = "completed"
    outcome: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    next_followup_date: datetime | None = None

    @field_validator("actual_time", "next_followup_date", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)


class CompleteFollowupProposal(ActionInput):
    customer_id: int = Field(ge=1)
    followup_id: int = Field(ge=1)

class ApplyEnrichmentProposal(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    customer_id: int = Field(ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)

    sales_stage: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    customer_status: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    enquiry_priority: EnquiryPriority | None = None
    enquiry_status: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    estimated_value: Decimal | None = Field(
        default=None,
        ge=0,
    )
