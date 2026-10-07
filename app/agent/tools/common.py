import json
from typing import Any

from app.core.exceptions import APIError

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
    return {
        "items": visible_records,
        "returned": len(visible_records),
        "truncated": len(records) > MAX_TOOL_RECORDS,
    }
