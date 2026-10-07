from datetime import datetime, timezone
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel

MAX_TEXT_LENGTH = 5000
MAX_MONETARY_VALUE = Decimal("9999999999.99")

ModelT = TypeVar("ModelT", bound=BaseModel)


def normalize_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize timezone-aware datetimes to UTC and treat naive datetimes as UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_monetary_decimal(value: Decimal | None) -> Decimal | None:
    """Validate Numeric(12, 2) non-negative monetary values."""
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("Monetary value must be a finite number.")
    if value < Decimal("0"):
        raise ValueError("Monetary value cannot be negative.")
    if value > MAX_MONETARY_VALUE:
        raise ValueError(f"Monetary value cannot exceed {MAX_MONETARY_VALUE}.")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("Monetary value can have at most 2 decimal places.")
    return value


def reject_explicit_nulls(model: ModelT, non_nullable_fields: tuple[str, ...]) -> ModelT:
    """Reject explicit null values for non-nullable database fields in partial update payloads."""
    for field_name in non_nullable_fields:
        if field_name in model.model_fields_set and getattr(model, field_name) is None:
            raise ValueError(f"{field_name} cannot be null.")
    return model
