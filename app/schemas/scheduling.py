from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.validators import validate_timezone_name


class ScheduleAvailabilityRequest(BaseModel):
    activity_type: Literal["meeting", "call", "followup"]
    starts_at: datetime
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    timezone_name: str = "UTC"
    exclude_id: int | None = Field(default=None, ge=1)

    @field_validator("timezone_name", mode="after")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)

    @model_validator(mode="after")
    def require_aware_start(self) -> "ScheduleAvailabilityRequest":
        if self.starts_at.tzinfo is None or self.starts_at.utcoffset() is None:
            raise ValueError("starts_at must include an explicit timezone.")
        return self


class ScheduleSlotResponse(BaseModel):
    starts_at: datetime
    ends_at: datetime
    timezone: str
    duration_minutes: int


class ScheduleAvailabilityResponse(BaseModel):
    available: bool
    activity_type: Literal["meeting", "call", "followup"]
    starts_at: datetime
    ends_at: datetime
    timezone: str
    duration_minutes: int
    conflicts: list[dict[str, object]]
    suggestions: list[ScheduleSlotResponse]


class ActivityMoveRequest(BaseModel):
    target_date: date
    target_timezone: str

    @field_validator("target_timezone", mode="after")
    @classmethod
    def validate_target_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)


class ActivityMoveResponse(BaseModel):
    activity_id: str
    starts_at: datetime
    timezone: str
