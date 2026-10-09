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

FollowUpStatus = Literal["pending", "in_progress", "completed", "cancelled", "overdue"]


class FollowUpInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class FollowUpCreate(FollowUpInput):
    customer_id: int = Field(ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    meeting_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)
    type: str = Field(min_length=1, max_length=50)
    due_date: datetime
    due_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: FollowUpStatus = "pending"
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    assigned_to: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None

    @field_validator("due_timezone", mode="after")
    @classmethod
    def validate_due_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @model_validator(mode="after")
    def set_due_timezone(self) -> "FollowUpCreate":
        if self.due_timezone is None:
            self.due_timezone = (
                timezone_name_from_datetime(self.due_date)
                if self.due_date.tzinfo is not None
                and self.due_date.utcoffset() is not None
                else "UTC"
            )
        self.due_date = normalize_utc_datetime(self.due_date)
        assert self.due_date is not None
        return self

    @field_validator("completed_at", mode="after")
    @classmethod
    def ensure_utc_completed_at(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)


class FollowUpUpdate(FollowUpInput):
    customer_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    meeting_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)
    type: str | None = Field(default=None, min_length=1, max_length=50)
    due_date: datetime | None = None
    due_timezone: str | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: FollowUpStatus | None = None
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    assigned_to: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None

    @field_validator("completed_at", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @field_validator("due_timezone", mode="after")
    @classmethod
    def validate_due_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "FollowUpUpdate":
        return reject_explicit_nulls(
            self,
            ("customer_id", "type", "due_date", "status", "due_timezone"),
        )


class FollowUpResponse(FollowUpCreate):
    model_config = ConfigDict(from_attributes=True)

    followup_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def render_due_date(self) -> "FollowUpResponse":
        value = self.due_date
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        self.due_date = value.astimezone(timezone_from_name(self.due_timezone or "UTC"))
        return self

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized
