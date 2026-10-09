from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
    timezone_name_from_datetime,
    timezone_from_name,
    validate_timezone_name,
)

CallStatus = Literal["scheduled", "attempted", "completed", "failed", "cancelled", "missed"]


class CallInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CallCreate(CallInput):
    customer_id: int = Field(ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    call_type: str | None = Field(default=None, max_length=50)
    scheduled_at: datetime | None = None
    actual_time: datetime | None = None
    scheduled_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: CallStatus = "scheduled"
    outcome: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    next_followup_date: datetime | None = None

    @field_validator("actual_time", "next_followup_date", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @field_validator("scheduled_timezone", mode="after")
    @classmethod
    def validate_scheduled_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @model_validator(mode="after")
    def set_schedule_timezone(self) -> "CallCreate":
        if self.scheduled_at is not None:
            if self.scheduled_timezone is None:
                self.scheduled_timezone = timezone_name_from_datetime(self.scheduled_at)
            self.scheduled_at = normalize_utc_datetime(self.scheduled_at)
        return self


class CallUpdate(CallInput):
    customer_id: int | None = Field(default=None, ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    call_type: str | None = Field(default=None, max_length=50)
    scheduled_at: datetime | None = None
    actual_time: datetime | None = None
    scheduled_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: CallStatus | None = None
    outcome: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    next_followup_date: datetime | None = None

    @field_validator("actual_time", "next_followup_date", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @field_validator("scheduled_timezone", mode="after")
    @classmethod
    def validate_scheduled_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "CallUpdate":
        return reject_explicit_nulls(
            self,
            ("customer_id", "status", "scheduled_timezone"),
        )


class CallResponse(CallCreate):
    model_config = ConfigDict(from_attributes=True)

    call_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def render_scheduled_time(self) -> "CallResponse":
        if self.scheduled_at is not None and self.scheduled_timezone is not None:
            value = self.scheduled_at
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=timezone.utc)
            self.scheduled_at = value.astimezone(timezone_from_name(self.scheduled_timezone))
        return self

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized
