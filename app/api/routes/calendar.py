from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.calendar import (
    CalendarConnectResponse,
    CalendarPreferencesUpdate,
    CalendarStatusResponse,
)
from app.services import calendar_sync_service
from app.services.calendar_sync_service import STATE_COOKIE, STATE_TTL

PREFIX = "/integrations/google-calendar"
router = APIRouter(prefix=PREFIX, tags=["google-calendar"])
# The OAuth callback is a browser redirect from Google without the API bearer
# token; it is authenticated by the signed state plus the HttpOnly nonce cookie.
callback_router = APIRouter(prefix=PREFIX, tags=["google-calendar"])


def _cookie_path() -> str:
    return f"{settings.api_prefix}{PREFIX}"


@router.get("", response_model=CalendarStatusResponse)
def get_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CalendarStatusResponse:
    return CalendarStatusResponse(**calendar_sync_service.connection_status(db, current_user.user_id))


@router.post("/connect", response_model=CalendarConnectResponse)
def connect(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> CalendarConnectResponse:
    url, nonce = calendar_sync_service.start_authorization(current_user)
    response.set_cookie(
        STATE_COOKIE,
        nonce,
        max_age=int(STATE_TTL.total_seconds()),
        path=_cookie_path(),
        httponly=True,
        secure=settings.environment.strip().lower() == "production",
        samesite="lax",
    )
    return CalendarConnectResponse(authorization_url=url)


@router.delete("", response_model=CalendarStatusResponse)
def disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CalendarStatusResponse:
    calendar_sync_service.disconnect(db, current_user.user_id)
    return CalendarStatusResponse(**calendar_sync_service.connection_status(db, current_user.user_id))


@router.put("/preferences", response_model=CalendarStatusResponse)
def update_preferences(
    data: CalendarPreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CalendarStatusResponse:
    calendar_sync_service.update_reminder(db, current_user.user_id, data.reminder_minutes)
    return CalendarStatusResponse(**calendar_sync_service.connection_status(db, current_user.user_id))


@router.post("/retry", response_model=CalendarStatusResponse)
def retry_failed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CalendarStatusResponse:
    calendar_sync_service.retry_failed(db, current_user.user_id)
    return CalendarStatusResponse(**calendar_sync_service.connection_status(db, current_user.user_id))


@callback_router.get("/callback", include_in_schema=False)
def oauth_callback(
    state: str = Query(default="", max_length=4096),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=200),
    cookie_nonce: str | None = Cookie(default=None, alias=STATE_COOKIE),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        outcome = {"calendar": "error", "reason": "access_denied"}
    elif not state or not code:
        outcome = {"calendar": "error", "reason": "calendar_oauth_state_invalid"}
    else:
        try:
            calendar_sync_service.complete_authorization(
                db, state=state, code=code, cookie_nonce=cookie_nonce
            )
            outcome = {"calendar": "connected"}
        except APIError as exc:
            outcome = {"calendar": "error", "reason": exc.code}
    redirect = RedirectResponse(f"/settings?{urlencode(outcome)}", status_code=303)
    redirect.delete_cookie(STATE_COOKIE, path=_cookie_path())
    return redirect
