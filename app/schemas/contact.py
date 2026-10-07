from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.validators import normalize_utc_datetime, reject_explicit_nulls


class ContactCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    job_title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    is_primary: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def blank_email_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ContactUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    job_title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    is_primary: bool | None = None

    @field_validator("email", mode="before")
    @classmethod
    def blank_email_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_non_nullable_fields(self) -> "ContactUpdate":
        return reject_explicit_nulls(self, ("customer_id", "name", "is_primary"))


class ContactResponse(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    contact_id: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timestamps(cls, value: datetime) -> datetime:
        normalized = normalize_utc_datetime(value)
        assert normalized is not None
        return normalized

