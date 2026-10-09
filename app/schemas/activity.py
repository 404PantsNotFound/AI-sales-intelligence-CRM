from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.call import CallResponse
from app.schemas.company import CompanyResponse
from app.schemas.contact import ContactResponse
from app.schemas.follow_up import FollowUpResponse
from app.schemas.meeting import MeetingResponse
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.schemas.validators import normalize_utc_datetime

ActivityType = Literal["enquiry", "meeting", "call", "follow_up"]


class ActivityResponse(BaseModel):
    activity_id: str
    activity_type: ActivityType
    activity_date: datetime
    activity_timezone: str | None = None
    status: str
    title: str
    description: str | None = None

    @field_validator("activity_date", mode="after")
    @classmethod
    def ensure_utc_activity_date(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized


class ActivityTimelineResponse(BaseModel):
    items: list[ActivityResponse]


class CustomerOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int = Field(ge=1)
    customer_name: str
    status: str
    sales_stage: str
    created_at: datetime
    updated_at: datetime
    company: CompanyResponse
    contacts: list[ContactResponse]
    sales_enquiries: list[SalesEnquiryResponse]
    meetings: list[MeetingResponse]
    calls: list[CallResponse]
    followups: list[FollowUpResponse]
    activity: ActivityTimelineResponse

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized


class ActivityFilter(BaseModel):
    type: ActivityType | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def date_range_is_valid(self) -> "ActivityFilter":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self
