from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.validators import (
    MAX_TEXT_LENGTH,
    normalize_utc_datetime,
    reject_explicit_nulls,
)


class CompanyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=150)
    website: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    company_size: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class CompanyUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str | None = Field(default=None, min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=150)
    website: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    company_size: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "CompanyUpdate":
        return reject_explicit_nulls(self, ("company_name",))


class CompanyResponse(CompanyCreate):
    model_config = ConfigDict(from_attributes=True)

    company_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

