from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.company import CompanyResponse
from app.schemas.contact import ContactResponse
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.schemas.validators import normalize_utc_datetime, reject_explicit_nulls

CustomerStatus = Literal["active", "inactive", "prospect"]
SalesStage = Literal["new", "qualified", "proposal", "negotiation", "won", "lost"]


class CustomerCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_id: int = Field(ge=1)
    customer_name: str = Field(min_length=1, max_length=255)
    status: CustomerStatus = "active"
    sales_stage: SalesStage = "new"


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_id: int | None = Field(default=None, ge=1)
    customer_name: str | None = Field(default=None, min_length=1, max_length=255)
    status: CustomerStatus | None = None
    sales_stage: SalesStage | None = None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "CustomerUpdate":
        return reject_explicit_nulls(
            self,
            ("company_id", "customer_name", "status", "sales_stage"),
        )


class CustomerResponse(CustomerCreate):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized


class CustomerDetailResponse(CustomerResponse):
    company: CompanyResponse
    contacts: list[ContactResponse]
    sales_enquiries: list[SalesEnquiryResponse]


class CustomerListItem(CustomerResponse):
    company: CompanyResponse


class CustomerListResponse(BaseModel):
    items: list[CustomerListItem]
    page: int
    page_size: int
    total: int

