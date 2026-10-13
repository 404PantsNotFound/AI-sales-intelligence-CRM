"""Minimal Google OAuth 2.0 and Calendar v3 client.

Tokens travel only in request bodies or the Authorization header, never in URLs,
and errors carry stable codes rather than Google's raw response text.
"""

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class GoogleCalendarError(Exception):
    def __init__(self, code: str, *, reauthorize: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.reauthorize = reauthorize


def _client_secret() -> str:
    secret = settings.google_client_secret
    return secret.get_secret_value() if secret is not None else ""


class GoogleCalendarClient:
    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": settings.google_client_id,
                "redirect_uri": settings.google_redirect_uri,
                "response_type": "code",
                "scope": settings.google_calendar_scope,
                "access_type": "offline",
                # Forces a refresh token on reconnect as well as first connect.
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
            }
        )
        return f"{AUTH_URL}?{query}"

    def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = httpx.post(
                TOKEN_URL,
                data=data,
                timeout=settings.google_api_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GoogleCalendarError("calendar_unavailable") from exc
        if response.status_code in {400, 401}:
            error = _json(response).get("error")
            if error in {"invalid_grant", "invalid_client", "unauthorized_client"}:
                raise GoogleCalendarError("calendar_reauthorization_required", reauthorize=True)
            raise GoogleCalendarError("calendar_oauth_rejected")
        if response.status_code >= 400:
            raise GoogleCalendarError("calendar_unavailable")
        return _json(response)

    def exchange_code(self, code: str) -> dict[str, Any]:
        return self._post_token(
            {
                "code": code,
                "client_id": settings.google_client_id or "",
                "client_secret": _client_secret(),
                "redirect_uri": settings.google_redirect_uri or "",
                "grant_type": "authorization_code",
            }
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._post_token(
            {
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id or "",
                "client_secret": _client_secret(),
                "grant_type": "refresh_token",
            }
        )

    def revoke(self, token: str) -> None:
        try:
            httpx.post(
                REVOKE_URL,
                data={"token": token},
                timeout=settings.google_api_timeout_seconds,
            )
        except httpx.HTTPError:
            # Best effort: the local tokens are deleted regardless.
            return

    def _events(
        self,
        method: str,
        access_token: str,
        event_id: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = EVENTS_URL if event_id is None else f"{EVENTS_URL}/{quote(event_id, safe='')}"
        try:
            response = httpx.request(
                method,
                url,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=settings.google_api_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GoogleCalendarError("calendar_unavailable") from exc
        if response.status_code == 401:
            raise GoogleCalendarError("calendar_reauthorization_required", reauthorize=True)
        if response.status_code == 403:
            raise GoogleCalendarError("calendar_forbidden")
        if response.status_code == 409:
            raise GoogleCalendarError("calendar_event_exists")
        if response.status_code in {404, 410}:
            raise GoogleCalendarError("calendar_event_not_found")
        if response.status_code >= 400:
            raise GoogleCalendarError("calendar_unavailable")
        return _json(response) if response.content else {}

    def insert_event(self, access_token: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._events("POST", access_token, body=body)

    def patch_event(self, access_token: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._events("PATCH", access_token, event_id, body)

    def delete_event(self, access_token: str, event_id: str) -> None:
        self._events("DELETE", access_token, event_id)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
