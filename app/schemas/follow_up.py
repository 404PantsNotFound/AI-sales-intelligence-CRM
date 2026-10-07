from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
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


class FollowUpUpdate(FollowUpInput):
    customer_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    meeting_id: int | None = Field(default=None, ge=1)
    call_id: int | None = Field(default=None, ge=1)
    type: str | None = Field(default=None, min_length=1, max_length=50)
    due_date: datetime | None = None
    status: FollowUpStatus | None = None
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    assigned_to: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None

    @field_validator("due_date", "completed_at", mode="after")
    @classmethod
    def ensure_utc_datetimes(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "FollowUpUpdate":
        return reject_explicit_nulls(
            self,
            ("customer_id", "type", "due_date", "status"),
        )


class FollowUpResponse(FollowUpCreate):
    model_config = ConfigDict(from_attributes=True)

    followup_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

