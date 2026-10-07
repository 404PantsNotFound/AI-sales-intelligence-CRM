from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
)

CallStatus = Literal["scheduled", "attempted", "completed", "failed", "cancelled"]


class CallInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CallCreate(CallInput):
    customer_id: int = Field(ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    call_type: str | None = Field(default=None, max_length=50)
    scheduled_at: datetime | None = None
    actual_time: datetime | None = None
    status: CallStatus = "scheduled"
    outcome: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    next_followup_date: datetime | None = None

    @field_validator("scheduled_at", "actual_time", "next_followup_date", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)


class CallUpdate(CallInput):
    customer_id: int | None = Field(default=None, ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    call_type: str | None = Field(default=None, max_length=50)
    scheduled_at: datetime | None = None
    actual_time: datetime | None = None
    status: CallStatus | None = None
    outcome: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    next_followup_date: datetime | None = None

    @field_validator("scheduled_at", "actual_time", "next_followup_date", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "CallUpdate":
        return reject_explicit_nulls(self, ("customer_id", "status"))


class CallResponse(CallCreate):
    model_config = ConfigDict(from_attributes=True)

    call_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

