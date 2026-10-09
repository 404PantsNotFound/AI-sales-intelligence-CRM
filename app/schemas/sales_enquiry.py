from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_MONETARY_VALUE,
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
    validate_monetary_decimal,
)

EnquiryPriority = Literal["low", "medium", "normal", "high", "urgent"]
EnquiryStatus = Literal["open", "in_progress", "converted", "closed", "lost"]


class SalesEnquiryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: int = Field(ge=1)
    product: str | None = Field(default=None, max_length=255)
    enquiry_text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    priority: EnquiryPriority = "normal"
    status: EnquiryStatus = "open"
    estimated_value: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=MAX_MONETARY_VALUE,
    )

    @field_validator("estimated_value", mode="after")
    @classmethod
    def check_monetary_value(cls, value: Decimal | None) -> Decimal | None:
        return validate_monetary_decimal(value)


class SalesEnquiryUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: int | None = Field(default=None, ge=1)
    product: str | None = Field(default=None, max_length=255)
    enquiry_text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_LENGTH)
    priority: EnquiryPriority | None = None
    status: EnquiryStatus | None = None
    estimated_value: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=MAX_MONETARY_VALUE,
    )

    @field_validator("estimated_value", mode="after")
    @classmethod
    def check_monetary_value(cls, value: Decimal | None) -> Decimal | None:
        return validate_monetary_decimal(value)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "SalesEnquiryUpdate":
        return reject_explicit_nulls(
            self,
            ("customer_id", "enquiry_text", "priority", "status"),
        )


class SalesEnquiryResponse(SalesEnquiryCreate):
    model_config = ConfigDict(from_attributes=True)

    enquiry_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized
