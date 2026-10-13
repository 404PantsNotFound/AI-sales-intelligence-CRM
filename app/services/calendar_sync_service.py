"""Per-user Google Calendar connection and one-way CRM -> Google event sync.

The CRM stays authoritative: events are written from CRM activities and nothing
is read back from Google. Sync runs after the CRM change has committed and never
raises into the caller; failures are recorded on the event link for retry.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.core.security import _get_jwt_secret
from app.models import (
    Call,
    CalendarEventLink,
    FollowUp,
    GoogleCalendarConnection,
    Meeting,
    User,
)
from app.schemas.validators import normalize_utc_datetime, timezone_from_name
from app.services.google_calendar_client import GoogleCalendarClient, GoogleCalendarError
from app.services.scheduling_service import default_duration, starts_in_past

logger = logging.getLogger(__name__)

ActivityType = Literal["meeting", "call", "followup"]
STATE_COOKIE = "gcal_oauth_nonce"
STATE_TTL = timedelta(minutes=10)
_STATE_PURPOSE = "google_calendar_oauth"
_ACCESS_TOKEN_MARGIN = timedelta(seconds=60)

google_client = GoogleCalendarClient()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _fernet() -> Fernet | None:
    key = settings.google_token_encryption_key
    if key is None:
        return None
    try:
        return Fernet(key.get_secret_value().encode())
    except (TypeError, ValueError):
        return None


def is_configured() -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret is not None
        and settings.google_client_secret.get_secret_value()
        and settings.google_redirect_uri
        and _fernet() is not None
    )


def _require_configured() -> Fernet:
    fernet = _fernet()
    if not is_configured() or fernet is None:
        raise APIError(
            "Google Calendar is not configured on this server.",
            503,
            "calendar_not_configured",
        )
    return fernet


def _encrypt(value: str) -> str:
    return _require_configured().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return _require_configured().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        # Wrong or rotated key: the stored tokens are unusable, so ask to reconnect.
        raise GoogleCalendarError("calendar_reauthorization_required", reauthorize=True) from exc


def _state_key() -> str:
    # A derived key, so an OAuth state can never be replayed as an API access token.
    return hmac.new(
        _get_jwt_secret().encode(), _STATE_PURPOSE.encode(), hashlib.sha256
    ).hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- OAuth connection -----------------------------------------------------------


def start_authorization(user: User) -> tuple[str, str]:
    """Return the Google consent URL and the browser-binding nonce for the cookie."""
    _require_configured()
    nonce = secrets.token_urlsafe(32)
    state = jwt.encode(
        {
            "uid": user.user_id,
            "purpose": _STATE_PURPOSE,
            "nonce_hash": _digest(nonce),
            "exp": int((_now() + STATE_TTL).timestamp()),
        },
        _state_key(),
        algorithm="HS256",
    )
    return google_client.authorization_url(state), nonce


def complete_authorization(
    db: Session,
    *,
    state: str,
    code: str,
    cookie_nonce: str | None,
) -> None:
    _require_configured()
    invalid = APIError(
        "The Google Calendar authorization request is invalid or expired.",
        400,
        "calendar_oauth_state_invalid",
    )
    try:
        claims = jwt.decode(state, _state_key(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise invalid from exc
    nonce_hash = claims.get("nonce_hash")
    user_id = claims.get("uid")
    if (
        claims.get("purpose") != _STATE_PURPOSE
        or not isinstance(nonce_hash, str)
        or not isinstance(user_id, int)
        or not cookie_nonce
        or not hmac.compare_digest(_digest(cookie_nonce), nonce_hash)
    ):
        raise invalid
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid

    try:
        tokens = google_client.exchange_code(code)
    except GoogleCalendarError as exc:
        raise APIError("Google did not accept the authorization.", 502, exc.code) from exc
    granted = str(tokens.get("scope") or "").split()
    if settings.google_calendar_scope not in granted:
        raise APIError(
            "Calendar access was not granted.",
            400,
            "calendar_scope_not_granted",
        )
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not isinstance(refresh_token, str) or not isinstance(access_token, str):
        raise APIError(
            "Google did not return the required tokens.",
            502,
            "calendar_token_missing",
        )

    connection = db.get(GoogleCalendarConnection, user_id)
    if connection is None:
        connection = GoogleCalendarConnection(user_id=user_id, reminder_minutes=10)
        db.add(connection)
    connection.encrypted_refresh_token = _encrypt(refresh_token)
    connection.encrypted_access_token = _encrypt(access_token)
    connection.access_token_expires_at = _now() + timedelta(
        seconds=int(tokens.get("expires_in") or 0)
    )
    connection.scope = " ".join(granted)[:512]
    connection.status = "connected"
    db.commit()


def connection_status(db: Session, user_id: int) -> dict[str, Any]:
    connection = db.get(GoogleCalendarConnection, user_id)
    failed = db.scalar(
        select(func.count())
        .select_from(CalendarEventLink)
        .where(
            CalendarEventLink.user_id == user_id,
            CalendarEventLink.sync_status == "failed",
        )
    )
    return {
        "configured": is_configured(),
        "connected": connection is not None,
        "status": connection.status if connection is not None else "disconnected",
        "reminder_minutes": connection.reminder_minutes if connection is not None else 10,
        "failed_syncs": int(failed or 0),
    }


def disconnect(db: Session, user_id: int) -> None:
    connection = db.get(GoogleCalendarConnection, user_id)
    if connection is None:
        return
    try:
        google_client.revoke(_decrypt(connection.encrypted_refresh_token))
    except (GoogleCalendarError, APIError):
        pass  # Revocation is best effort; the local tokens are deleted either way.
    # Existing Google events are left in place; their links keep the event IDs so a
    # later reconnect updates the same events instead of duplicating them.
    db.delete(connection)
    db.commit()


def update_reminder(db: Session, user_id: int, reminder_minutes: int) -> None:
    connection = db.get(GoogleCalendarConnection, user_id)
    if connection is None:
        raise APIError("Google Calendar is not connected.", 404, "calendar_not_connected")
    connection.reminder_minutes = reminder_minutes
    db.commit()


def _access_token(db: Session, connection: GoogleCalendarConnection) -> str:
    if connection.status != "connected":
        raise GoogleCalendarError("calendar_reauthorization_required", reauthorize=True)
    if (
        connection.encrypted_access_token
        and connection.access_token_expires_at is not None
        and _utc(connection.access_token_expires_at) > _now() + _ACCESS_TOKEN_MARGIN
    ):
        return _decrypt(connection.encrypted_access_token)
    tokens = google_client.refresh(_decrypt(connection.encrypted_refresh_token))
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str):
        raise GoogleCalendarError("calendar_token_missing")
    connection.encrypted_access_token = _encrypt(access_token)
    connection.access_token_expires_at = _now() + timedelta(
        seconds=int(tokens.get("expires_in") or 0)
    )
    if isinstance(tokens.get("refresh_token"), str):
        connection.encrypted_refresh_token = _encrypt(tokens["refresh_token"])
    return access_token


# --- Event mapping -----------------------------------------------------------------


def event_id_for(user_id: int, activity_type: str, activity_id: int) -> str:
    # Deterministic and valid for Google (base32hex subset), so a retried insert
    # can only ever collide with its own earlier event, never duplicate it.
    return hashlib.sha256(
        f"ai-sales-crm:{user_id}:{activity_type}:{activity_id}".encode()
    ).hexdigest()


def _load_activity(
    db: Session, activity_type: ActivityType, activity_id: int
) -> Meeting | Call | FollowUp | None:
    model = {"meeting": Meeting, "call": Call, "followup": FollowUp}[activity_type]
    return db.get(model, activity_id)


def _schedule(activity_type: ActivityType, activity: Any) -> tuple[datetime | None, str, int]:
    if activity_type == "followup":
        start, zone = activity.due_date, activity.due_timezone
    else:
        start, zone = activity.scheduled_at, activity.scheduled_timezone
    return (
        normalize_utc_datetime(start),
        zone or "UTC",
        activity.duration or default_duration(activity_type),
    )


def _wants_event(activity_type: ActivityType, activity: Any) -> bool:
    active = {
        "meeting": ("scheduled",),
        "call": ("scheduled",),
        "followup": ("pending", "in_progress"),
    }[activity_type]
    start, _, _ = _schedule(activity_type, activity)
    return activity.status in active and start is not None and not starts_in_past(start)


def _time(moment: datetime, zone_name: str) -> dict[str, str]:
    local = moment.astimezone(timezone_from_name(zone_name))
    value = {"dateTime": local.isoformat()}
    if "/" in zone_name or zone_name in {"UTC", "Etc/UTC"}:
        value["timeZone"] = zone_name  # Google needs IANA names; offsets live in dateTime.
    return value


def _event_body(
    activity_type: ActivityType, activity_id: int, activity: Any, reminder_minutes: int
) -> dict[str, Any]:
    start, zone, duration = _schedule(activity_type, activity)
    assert start is not None
    customer = activity.customer.customer_name if activity.customer else "CRM customer"
    lines: list[str] = []
    if activity_type == "meeting":
        summary = f"Meeting with {customer}"
        lines += [f"Agenda: {activity.agenda}" if activity.agenda else "", activity.notes or ""]
    elif activity_type == "call":
        summary = f"Call with {customer}"
        lines += [f"Call type: {activity.call_type}" if activity.call_type else "", activity.notes or ""]
    else:
        summary = f"Follow-up ({activity.type}) for {customer}"
        lines += [activity.description or ""]
    lines.append(f"Customer: {customer}")
    contact = getattr(activity, "contact", None)
    if contact is not None:
        details = ", ".join(
            part for part in (contact.name, contact.job_title, contact.email, contact.phone) if part
        )
        lines.append(f"Contact: {details}")
    lines.append("Managed by AI Sales CRM; edits made in Google Calendar are not copied back.")
    return {
        "summary": summary[:1024],
        "description": "\n".join(line for line in lines if line)[:8000],
        "start": _time(start, zone),
        "end": _time(start + timedelta(minutes=duration), zone),
        "status": "confirmed",
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": reminder_minutes}],
        },
        "extendedProperties": {
            "private": {"crm_activity": f"{activity_type}:{activity_id}"}
        },
    }


# --- Sync --------------------------------------------------------------------------


def _sync_for_user(
    db: Session,
    user_id: int,
    activity_type: ActivityType,
    activity_id: int,
    link: CalendarEventLink | None,
) -> None:
    connection = db.get(GoogleCalendarConnection, user_id)
    if connection is None:
        return
    activity = _load_activity(db, activity_type, activity_id)
    wants_event = activity is not None and _wants_event(activity_type, activity)
    cancelled = activity is None or activity.status == "cancelled"
    if link is None and not wants_event:
        return  # Never create events for history, cancellations or past times.
    if not wants_event and not (cancelled and link is not None and link.sync_status != "deleted"):
        return  # Completed/no-show history keeps its existing event untouched.

    if link is None:
        link = CalendarEventLink(
            user_id=user_id,
            activity_type=activity_type,
            activity_id=activity_id,
            google_event_id=event_id_for(user_id, activity_type, activity_id),
            sync_status="failed",
            attempts=0,
        )
        db.add(link)
    link.attempts = (link.attempts or 0) + 1
    try:
        access_token = _access_token(db, connection)
        if wants_event:
            body = _event_body(activity_type, activity_id, activity, connection.reminder_minutes)
            if link.synced_at is None:
                try:
                    google_client.insert_event(access_token, {"id": link.google_event_id, **body})
                except GoogleCalendarError as exc:
                    if exc.code != "calendar_event_exists":
                        raise
                    # An earlier attempt reached Google but was not recorded here.
                    google_client.patch_event(access_token, link.google_event_id, body)
            else:
                google_client.patch_event(access_token, link.google_event_id, body)
            link.sync_status = "synced"
        else:
            try:
                google_client.delete_event(access_token, link.google_event_id)
            except GoogleCalendarError as exc:
                if exc.code != "calendar_event_not_found":
                    raise
            link.sync_status = "deleted"
        link.last_error = None
        link.synced_at = _now()
    except GoogleCalendarError as exc:
        if exc.reauthorize:
            connection.status = "reauthorization_required"
        link.sync_status = "failed"
        link.last_error = exc.code
        logger.warning(
            "Google Calendar sync failed [user_id=%s activity=%s:%s code=%s]",
            user_id,
            activity_type,
            activity_id,
            exc.code,
        )
    db.commit()


def sync_activity(
    db: Session,
    activity_type: ActivityType,
    activity_id: int,
    *,
    created_by_user_id: int | None = None,
) -> None:
    """Mirror one committed CRM activity to Google Calendar.

    New activities create an event only for ``created_by_user_id``; edits and
    cancellations update every calendar that already holds an event for it.
    """
    if not is_configured():
        return
    try:
        links = {
            link.user_id: link
            for link in db.scalars(
                select(CalendarEventLink).where(
                    CalendarEventLink.activity_type == activity_type,
                    CalendarEventLink.activity_id == activity_id,
                )
            ).all()
        }
        user_ids = set(links)
        if created_by_user_id is not None:
            user_ids.add(created_by_user_id)
        for user_id in sorted(user_ids):
            _sync_for_user(db, user_id, activity_type, activity_id, links.get(user_id))
    except (SQLAlchemyError, APIError) as exc:
        db.rollback()
        logger.error(
            "Google Calendar sync could not be recorded [activity=%s:%s error_type=%s]",
            activity_type,
            activity_id,
            type(exc).__name__,
        )


def retry_failed(db: Session, user_id: int) -> None:
    _require_configured()
    links = db.scalars(
        select(CalendarEventLink).where(
            CalendarEventLink.user_id == user_id,
            CalendarEventLink.sync_status == "failed",
        )
    ).all()
    for link in links:
        _sync_for_user(db, user_id, link.activity_type, link.activity_id, link)
