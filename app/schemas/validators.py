from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
import re
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

MAX_TEXT_LENGTH = 5000
MAX_MONETARY_VALUE = Decimal("9999999999.99")

ModelT = TypeVar("ModelT", bound=BaseModel)
_UTC_OFFSET_PATTERN = re.compile(r"^[+-](?:0\d|1[0-4]):[0-5]\d$")


class NonexistentLocalTimeError(ValueError):
    pass


class AmbiguousLocalTimeError(ValueError):
    pass


def validate_timezone_name(value: str) -> str:
    cleaned = value.strip()
    if cleaned in {"UTC", "Etc/UTC"}:
        return cleaned
    if _UTC_OFFSET_PATTERN.fullmatch(cleaned):
        hours, minutes = (int(part) for part in cleaned[1:].split(":"))
        if hours < 14 or minutes == 0:
            return cleaned
        raise ValueError("Timezone offsets cannot exceed 14:00.")
    try:
        ZoneInfo(cleaned)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Use a valid IANA timezone or UTC offset.") from exc
    return cleaned


def timezone_from_name(value: str) -> tzinfo:
    cleaned = validate_timezone_name(value)
    if cleaned in {"UTC", "Etc/UTC"}:
        return timezone.utc
    if cleaned.startswith(("+", "-")):
        sign = 1 if cleaned[0] == "+" else -1
        hours, minutes = (int(part) for part in cleaned[1:].split(":"))
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    return ZoneInfo(cleaned)


def timezone_name_from_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Meeting date and time must include a timezone.")
    zone_key = getattr(value.tzinfo, "key", None)
    if isinstance(zone_key, str):
        return validate_timezone_name(zone_key)
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() % 60:
        raise ValueError("Meeting timezone offsets must use whole minutes.")
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def resolve_local_datetime(
    local_date: date,
    local_time: time,
    timezone_name: str,
    occurrence: str | None = None,
) -> datetime:
    if local_time.tzinfo is not None:
        raise ValueError("Provide a local wall-clock time without an embedded timezone.")
    zone = timezone_from_name(timezone_name)
    local = datetime.combine(local_date, local_time)
    if not isinstance(zone, ZoneInfo):
        return local.replace(tzinfo=zone)

    candidates = [
        local.replace(tzinfo=zone, fold=fold)
        for fold in (0, 1)
    ]
    valid_candidates = [
        candidate
        for candidate in candidates
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        == local
    ]
    distinct_candidates = {
        candidate.astimezone(timezone.utc): candidate
        for candidate in valid_candidates
    }
    if not distinct_candidates:
        raise NonexistentLocalTimeError(
            "The local time does not exist because of a daylight-saving transition."
        )
    if len(distinct_candidates) == 1:
        return next(iter(distinct_candidates.values()))
    if occurrence not in {"earlier", "later"}:
        raise AmbiguousLocalTimeError(
            "The local time occurs twice because of a daylight-saving transition."
        )

    ordered = sorted(distinct_candidates.items(), key=lambda item: item[0])
    return ordered[0 if occurrence == "earlier" else -1][1]


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
