import logging
import re

from app.core.config import settings

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)(password|passwd|pwd|secret|api_key|apikey|token)\s*[=:]\s*\S+"),
)


def _configured_secrets() -> list[str]:
    values: list[str] = []
    for secret in (
        settings.gemini_api_key,
        settings.openai_api_key,
        settings.jwt_secret_key,
        settings.db_password,
    ):
        if secret is not None:
            raw = secret.get_secret_value().strip()
            if raw:
                values.append(raw)
    return values


def contains_sensitive_material(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "secret", "api_key", "apikey", "sk-", "bearer ")):
        return True
    return any(secret in text for secret in _configured_secrets())


def redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for secret in _configured_secrets():
        redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def log_database_exception(
    logger: logging.Logger,
    operation: str,
    exc: Exception,
    *,
    conflict: bool = False,
) -> None:
    raw_message = str(exc)
    if conflict:
        logger.error(
            "Database integrity conflict [operation=%s error_type=%s]",
            operation,
            type(exc).__name__,
        )
        return

    if contains_sensitive_material(raw_message):
        logger.error(
            "Database operation failed [operation=%s error_type=%s detail=%s]",
            operation,
            type(exc).__name__,
            redact_sensitive_text(raw_message),
        )
    else:
        logger.exception(
            "Database operation failed [operation=%s error_type=%s]",
            operation,
            type(exc).__name__,
        )
