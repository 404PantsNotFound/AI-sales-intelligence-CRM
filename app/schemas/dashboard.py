from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.validators import normalize_utc_datetime, validate_timezone_name


class DashboardActivityResponse(BaseModel):
    activity_id: str
    activity_type: Literal["meeting", "call", "follow_up"]
    customer_id: int
    customer_name: str
    company_name: str
    starts_at: datetime
    timezone: str
    duration_minutes: int = Field(gt=0, le=1440)
    status: str
    title: str
    description: str | None = None

    @field_validator("starts_at", mode="after")
    @classmethod
    def require_aware_start(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

    @field_validator("timezone", mode="after")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)


class DashboardActivityListResponse(BaseModel):
    items: list[DashboardActivityResponse]


class DashboardActivityRange(BaseModel):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "DashboardActivityRange":
        if (
            self.start_at.tzinfo is None
            or self.start_at.utcoffset() is None
            or self.end_at.tzinfo is None
            or self.end_at.utcoffset() is None
        ):
            raise ValueError("Dashboard activity range must include explicit timezones.")
        if self.start_at.astimezone(timezone.utc) >= self.end_at.astimezone(timezone.utc):
            raise ValueError("end_at must be after start_at.")
        return self
