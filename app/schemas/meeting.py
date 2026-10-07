from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
)

MeetingStatus = Literal["scheduled", "completed", "cancelled", "no_show"]


class MeetingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class MeetingCreate(MeetingInput):
    customer_id: int = Field(ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: MeetingStatus = "scheduled"
    agenda: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def ensure_utc_scheduled_at(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized


class MeetingUpdate(MeetingInput):
    customer_id: int | None = Field(default=None, ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)
    scheduled_at: datetime | None = None
    duration: int | None = Field(default=None, gt=0, le=1440)
    status: MeetingStatus | None = None
    agenda: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def ensure_utc_scheduled_at(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "MeetingUpdate":
        return reject_explicit_nulls(self, ("customer_id", "scheduled_at", "status"))


class MeetingResponse(MeetingCreate):
    model_config = ConfigDict(from_attributes=True)

    meeting_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

