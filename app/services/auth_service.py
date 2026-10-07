import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.core.logging_utils import log_database_exception
from app.core.security import create_access_token, hash_password, verify_password
from app.database.connection import atomic_transaction
from app.models import User
from app.schemas.auth import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

logger = logging.getLogger(__name__)


def _normalized_email(email: str) -> str:
    return email.strip().lower()


def register_user(db: Session, payload: UserRegisterRequest) -> User:
    if not settings.allow_public_registration:
        raise APIError(
            "Public user registration is disabled.",
            status_code=403,
            code="registration_disabled",
        )
    if (
        settings.environment.strip().lower() == "production"
        and not settings.allow_public_registration_in_production
    ):
        raise APIError(
            "Public user registration is disabled in production.",
            status_code=403,
            code="registration_disabled",
        )

    normalized_email = _normalized_email(payload.email)
    try:
        with atomic_transaction(db):
            existing_id = db.scalar(
                select(User.user_id)
                .where(func.lower(func.trim(User.email)) == normalized_email)
                .limit(1)
            )
            if existing_id is not None:
                raise APIError(
                    "A user with this email is already registered.",
                    status_code=409,
                    code="duplicate_email",
                )

            user = User(
                email=normalized_email,
                full_name=payload.full_name,
                password_hash=hash_password(payload.password),
                role=payload.role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.refresh(user)
        return user
    except APIError:
        raise
    except IntegrityError as exc:
        log_database_exception(logger, "register_user", exc, conflict=True)
        raise APIError(
            "A user with this email is already registered.",
            status_code=409,
            code="duplicate_email",
        ) from exc
    except SQLAlchemyError as exc:
        log_database_exception(logger, "register_user", exc)
        raise APIError(
            "The database operation could not be completed.",
            status_code=500,
            code="database_error",
        ) from exc


def authenticate_user(db: Session, payload: UserLoginRequest) -> TokenResponse:
    normalized_email = _normalized_email(payload.email)
    try:
        user = db.scalar(
            select(User)
            .where(func.lower(func.trim(User.email)) == normalized_email)
            .limit(1)
        )
    except SQLAlchemyError as exc:
        log_database_exception(logger, "authenticate_user", exc)
        raise APIError(
            "The database operation could not be completed.",
            status_code=500,
            code="database_error",
        ) from exc


    if user is None or not verify_password(payload.password, user.password_hash):
        raise APIError(
            "Invalid email or password.",
            status_code=401,
            code="invalid_credentials",
        )

    if not user.is_active:
        raise APIError(
            "User account is inactive.",
            status_code=403,
            code="inactive_user",
        )

    access_token, expires_in = create_access_token(user)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )
