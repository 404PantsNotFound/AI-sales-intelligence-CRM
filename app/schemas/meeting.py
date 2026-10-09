from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
    timezone_from_name,
    timezone_name_from_datetime,
    validate_timezone_name,
)

MeetingStatus = Literal["scheduled", "completed", "cancelled", "no_show"]


class MeetingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class MeetingCreate(MeetingInput):
    customer_id: int = Field(ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime
    scheduled_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: MeetingStatus = "scheduled"
    agenda: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @model_validator(mode="after")
    def require_aware_schedule(self) -> "MeetingCreate":
        if self.scheduled_at.tzinfo is None or self.scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_at must include an explicit timezone.")
        if self.scheduled_timezone is None:
            self.scheduled_timezone = timezone_name_from_datetime(self.scheduled_at)
        else:
            self.scheduled_timezone = validate_timezone_name(self.scheduled_timezone)
        return self


class MeetingUpdate(MeetingInput):
    customer_id: int | None = Field(default=None, ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime | None = None
    scheduled_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: MeetingStatus | None = None
    agenda: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def require_aware_schedule(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include an explicit timezone.")
        return value

    @field_validator("scheduled_timezone", mode="after")
    @classmethod
    def validate_schedule_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "MeetingUpdate":
        return reject_explicit_nulls(self, ("customer_id", "scheduled_at", "status"))


class MeetingResponse(MeetingCreate):
    model_config = ConfigDict(from_attributes=True)

    meeting_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def assume_database_schedule_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def render_in_scheduled_timezone(self) -> "MeetingResponse":
        self.scheduled_at = self.scheduled_at.astimezone(
            timezone_from_name(self.scheduled_timezone or "UTC")
        )
        return self

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized
