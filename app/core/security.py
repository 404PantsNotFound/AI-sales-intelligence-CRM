from datetime import datetime, timedelta, timezone
import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.database.connection import get_db, rollback_failed_transaction
from app.models import User

logger = logging.getLogger(__name__)
_password_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)



def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _get_jwt_secret() -> str:
    secret = settings.jwt_secret_key.get_secret_value().strip()
    if not secret:
        raise APIError(
            "JWT secret key is not configured.",
            status_code=500,
            code="auth_configuration_error",
        )
    return secret


def create_access_token(
    user: User,
    *,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    secret = _get_jwt_secret()
    ttl = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    now = datetime.now(timezone.utc)
    expire = now + ttl
    payload = {
        "sub": str(user.user_id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
    return token, max(0, int(ttl.total_seconds()))


def decode_access_token(token: str) -> dict[str, object]:
    secret = _get_jwt_secret()
    try:
        decoded = jwt.decode(
            token,
            secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        if not isinstance(decoded, dict):
            raise APIError(
                "Invalid authentication token.",
                status_code=401,
                code="invalid_token",
            )
        return decoded
    except jwt.ExpiredSignatureError as exc:
        raise APIError(
            "Authentication token has expired.",
            status_code=401,
            code="token_expired",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise APIError(
            "Invalid authentication token.",
            status_code=401,
            code="invalid_token",
        ) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials.strip()
    ):
        raise APIError(
            "Authentication credentials were not provided.",
            status_code=401,
            code="authentication_required",
        )

    payload = decode_access_token(credentials.credentials.strip())
    raw_sub = payload.get("sub")
    try:
        user_id = int(str(raw_sub))
    except (TypeError, ValueError) as exc:
        raise APIError(
            "Invalid authentication token subject.",
            status_code=401,
            code="invalid_token",
        ) from exc

    if user_id <= 0:
        raise APIError(
            "Invalid authentication token subject.",
            status_code=401,
            code="invalid_token",
        )

    try:
        user = db.get(User, user_id)
    except SQLAlchemyError as exc:
        rollback_failed_transaction(db)
        log_database_exception(logger, "get_current_user", exc)
        raise APIError(
            "The database operation could not be completed.",
            status_code=500,
            code="database_error",
        ) from exc


    if user is None:
        raise APIError(
            "Authenticated user no longer exists.",
            status_code=401,
            code="invalid_token",
        )
    if not user.is_active:
        raise APIError(
            "User account is inactive.",
            status_code=403,
            code="inactive_user",
        )
    return user
