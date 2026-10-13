import json
from datetime import datetime
from typing import Any

from app.core.exceptions import APIError
from app.services.scheduling_service import starts_in_past

MAX_TOOL_RECORDS = 25
MAX_TOOL_TEXT_LENGTH = 2000


def success_output(data: Any) -> str:
    return json.dumps({"data": data}, ensure_ascii=False)


def error_output(error: APIError) -> str:
    return json.dumps(
        {"error": {"code": error.code, "message": error.message}},
        ensure_ascii=False,
    )


def bounded_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    visible_records = records[:MAX_TOOL_RECORDS]
    for record in visible_records:
        for field, value in record.items():
            if isinstance(value, str) and len(value) > MAX_TOOL_TEXT_LENGTH:
                record[field] = value[:MAX_TOOL_TEXT_LENGTH] + "…"
    result: dict[str, Any] = {
        "items": visible_records,
        "returned": len(visible_records),
        "truncated": len(records) > MAX_TOOL_RECORDS,
    }
    if not records:
        # Explicit, so "nothing found" is not filled in with invented records.
        result["note"] = "No matching CRM records exist."
    return result


def with_timing(records: list[dict[str, Any]], time_field: str) -> list[dict[str, Any]]:
    """Mark each record past or upcoming against the server clock; status is separate."""
    for record in records:
        value = record.get(time_field)
        if isinstance(value, str):
            record["timing"] = (
                "past" if starts_in_past(datetime.fromisoformat(value)) else "upcoming"
            )
    return records
