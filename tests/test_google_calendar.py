from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.main import app
from app.models import (
    CalendarEventLink,
    GoogleCalendarConnection,
    HitlActionProposal,
)
from app.services import calendar_sync_service
from app.services.google_calendar_client import GoogleCalendarError
from tests.conftest import authorize_test_client
from tests.test_agent_actions import _chat_model, _post_chat, _tool_call

SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
FUTURE = "2030-11-05T10:00:00+04:00"


class FakeGoogle:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.failures: list[GoogleCalendarError] = []
        self.refresh_error: GoogleCalendarError | None = None
        self.granted_scope = SCOPE
        self.revoked: list[str] = []

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.google.test/auth?state={state}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        self.calls.append(("exchange", code))
        return {
            "access_token": f"access-{code}",
            "refresh_token": f"refresh-{code}",
            "expires_in": 3600,
            "scope": self.granted_scope,
        }

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        if self.refresh_error is not None:
            raise self.refresh_error
        self.calls.append(("refresh", refresh_token))
        return {"access_token": "access-refreshed", "expires_in": 3600}

    def revoke(self, token: str) -> None:
        self.revoked.append(token)

    def _maybe_fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    def insert_event(self, access_token: str, body: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail()
        if body["id"] in self.events:
            raise GoogleCalendarError("calendar_event_exists")
        self.events[body["id"]] = body
        self.calls.append(("insert", body["id"]))
        return body

    def patch_event(self, access_token: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail()
        self.events[event_id] = {**self.events.get(event_id, {}), **body}
        self.calls.append(("patch", event_id))
        return self.events[event_id]

    def delete_event(self, access_token: str, event_id: str) -> None:
        self._maybe_fail()
        self.events[event_id]["status"] = "cancelled"
        self.calls.append(("delete", event_id))


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret"))
    monkeypatch.setattr(
        settings,
        "google_redirect_uri",
        "http://testserver/api/integrations/google-calendar/callback",
    )
    monkeypatch.setattr(
        settings,
        "google_token_encryption_key",
        SecretStr(Fernet.generate_key().decode()),
    )
    fake = FakeGoogle()
    monkeypatch.setattr(calendar_sync_service, "google_client", fake)
    return fake


def _connect(client: TestClient, code: str = "code-a") -> Any:
    started = client.post("/api/integrations/google-calendar/connect")
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    return client.get(
        "/api/integrations/google-calendar/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )


def _status(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/integrations/google-calendar")
    assert response.status_code == 200
    return response.json()


def _links(sessions: sessionmaker[Session]) -> list[CalendarEventLink]:
    with sessions() as db:
        return list(db.scalars(select(CalendarEventLink)).all())


def _create(client: TestClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_connect_requires_authentication_and_configuration(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Independent of a developer's real Google values in .env.
    monkeypatch.setattr(settings, "google_client_id", None)
    unconfigured = agent_client.post("/api/integrations/google-calendar/connect")
    assert unconfigured.status_code == 503
    assert unconfigured.json()["error"]["code"] == "calendar_not_configured"
    assert _status(agent_client)["configured"] is False

    agent_client.headers.pop("Authorization")
    assert agent_client.post("/api/integrations/google-calendar/connect").status_code == 401


def test_oauth_state_is_signed_browser_bound_and_never_an_access_token(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    google: FakeGoogle,
) -> None:
    started = agent_client.post("/api/integrations/google-calendar/connect")
    cookie = started.headers["set-cookie"]
    assert "gcal_oauth_nonce=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]

    # The state is useless as an API bearer token.
    assert agent_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {state}"}
    ).status_code == 401

    callback = "/api/integrations/google-calendar/callback"
    tampered = agent_client.get(
        callback, params={"state": state[:-2] + "xx", "code": "c"}, follow_redirects=False
    )
    assert tampered.headers["location"] == "/settings?calendar=error&reason=calendar_oauth_state_invalid"

    # The same state from a different browser (no nonce cookie) is rejected.
    with TestClient(app) as other_browser:
        replayed = other_browser.get(
            callback, params={"state": state, "code": "c"}, follow_redirects=False
        )
    assert replayed.headers["location"].endswith("reason=calendar_oauth_state_invalid")
    assert ("exchange", "c") not in google.calls

    # Every callback clears the single-use nonce cookie, so the valid path starts afresh.
    assert agent_client.cookies.get("gcal_oauth_nonce") is None
    connected = _connect(agent_client, "c")
    assert connected.headers["location"] == "/settings?calendar=connected"

    with agent_sessions() as db:
        row = db.scalars(select(GoogleCalendarConnection)).one()
        assert row.encrypted_refresh_token != "refresh-c"
        assert "refresh-c" not in row.encrypted_refresh_token
        fernet = Fernet(settings.google_token_encryption_key.get_secret_value().encode())
        assert fernet.decrypt(row.encrypted_refresh_token.encode()).decode() == "refresh-c"
    status = agent_client.get("/api/integrations/google-calendar")
    assert status.json() == {
        "configured": True,
        "connected": True,
        "status": "connected",
        "reminder_minutes": 10,
        "failed_syncs": 0,
    }
    assert "access-c" not in status.text and "refresh-c" not in status.text


def test_callback_rejects_missing_calendar_scope(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    google: FakeGoogle,
) -> None:
    google.granted_scope = "openid"
    response = _connect(agent_client)
    assert response.headers["location"].endswith("reason=calendar_scope_not_granted")
    with agent_sessions() as db:
        assert db.scalars(select(GoogleCalendarConnection)).first() is None


def test_connections_events_and_retries_are_isolated_per_user(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    owner = _status(agent_client)
    assert owner["connected"] is True

    authorize_test_client(agent_sessions, agent_client, email="second.rep@example.com")
    assert _status(agent_client)["connected"] is False
    # User B's actions never touch user A's connection or calendar.
    agent_client.delete("/api/integrations/google-calendar")
    _create(
        agent_client,
        "/api/meetings",
        {"customer_id": agent_records["customer_id"], "scheduled_at": FUTURE},
    )
    assert google.events == {} and google.revoked == []
    with agent_sessions() as db:
        assert db.scalars(select(GoogleCalendarConnection)).one() is not None


def test_activities_sync_with_timezone_details_and_reminder_preference(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    customer_id = agent_records["customer_id"]
    meeting = _create(
        agent_client,
        "/api/meetings",
        {
            "customer_id": customer_id,
            "contact_id": agent_records["contact_id"],
            "scheduled_at": FUTURE,
            "scheduled_timezone": "Asia/Dubai",
            "duration": 45,
            "agenda": "Pricing review",
        },
    )
    event = google.events[
        calendar_sync_service.event_id_for(1, "meeting", meeting["meeting_id"])
    ]
    assert event["summary"] == "Meeting with ABC Trading Customer"
    assert event["start"] == {"dateTime": "2030-11-05T10:00:00+04:00", "timeZone": "Asia/Dubai"}
    assert event["end"] == {"dateTime": "2030-11-05T10:45:00+04:00", "timeZone": "Asia/Dubai"}
    assert "Agenda: Pricing review" in event["description"]
    assert "Contact: Alex Smith, Buyer, alex@example.com" in event["description"]
    assert event["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 10}],
    }

    preference = agent_client.put(
        "/api/integrations/google-calendar/preferences", json={"reminder_minutes": 30}
    )
    assert preference.json()["reminder_minutes"] == 30
    assert agent_client.put(
        "/api/integrations/google-calendar/preferences", json={"reminder_minutes": 5000}
    ).status_code == 422

    _create(
        agent_client,
        "/api/calls",
        {"customer_id": customer_id, "scheduled_at": "2030-11-06T09:00:00Z", "status": "scheduled"},
    )
    _create(
        agent_client,
        "/api/followups",
        {"customer_id": customer_id, "type": "proposal", "due_date": "2030-11-07T09:00:00Z"},
    )
    later = [body for event_id, body in google.events.items() if not body["summary"].startswith("Meeting")]
    assert {body["summary"] for body in later} == {
        "Call with ABC Trading Customer",
        "Follow-up (proposal) for ABC Trading Customer",
    }
    assert all(body["reminders"]["overrides"][0]["minutes"] == 30 for body in later)
    assert {link.sync_status for link in _links(agent_sessions)} == {"synced"}


def test_history_and_inactive_activities_are_not_synced(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    customer_id = agent_records["customer_id"]
    _create(
        agent_client,
        "/api/meetings",
        {"customer_id": customer_id, "scheduled_at": FUTURE, "status": "completed"},
    )
    _create(
        agent_client,
        "/api/followups",
        {"customer_id": customer_id, "type": "late", "due_date": "2026-08-07T10:00:00Z", "status": "overdue"},
    )
    assert google.events == {} and _links(agent_sessions) == []


def test_hitl_event_is_created_only_after_approval(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect(agent_client)
    customer_id = agent_records["customer_id"]

    def propose() -> str:
        _chat_model(
            monkeypatch,
            _tool_call(
                "schedule_call",
                {"customer_id": customer_id, "scheduled_at": FUTURE, "scheduled_timezone": "Asia/Dubai"},
            ),
        )
        return _post_chat(agent_client, customer_id, "Schedule a call")["pending_action"]["action_id"]

    rejected = propose()
    assert google.events == {}
    assert agent_client.post(f"/api/agent/actions/{rejected}/reject").status_code == 200
    assert google.events == {}

    approved = propose()
    assert google.events == {}
    confirmed = agent_client.post(f"/api/agent/actions/{approved}/confirm")
    assert confirmed.json()["status"] == "completed"
    assert len(google.events) == 1
    with agent_sessions() as db:
        owner = db.get(HitlActionProposal, approved).owner_user_id
    assert next(iter(google.events)) == calendar_sync_service.event_id_for(
        owner, "call", confirmed.json()["record_id"]
    )


def test_failed_sync_keeps_crm_write_and_retry_never_duplicates(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    google.failures.append(GoogleCalendarError("calendar_unavailable"))
    meeting = _create(
        agent_client,
        "/api/meetings",
        {"customer_id": agent_records["customer_id"], "scheduled_at": FUTURE},
    )
    assert google.events == {}
    assert _status(agent_client)["failed_syncs"] == 1
    [link] = _links(agent_sessions)
    assert (link.sync_status, link.last_error) == ("failed", "calendar_unavailable")

    # Google may have created the event even though the response never arrived.
    event_id = calendar_sync_service.event_id_for(1, "meeting", meeting["meeting_id"])
    google.events[event_id] = {"id": event_id, "summary": "stale"}
    retried = agent_client.post("/api/integrations/google-calendar/retry")
    assert retried.json()["failed_syncs"] == 0
    assert len(google.events) == 1
    assert google.events[event_id]["summary"] == "Meeting with ABC Trading Customer"
    assert ("patch", event_id) in google.calls


def test_expired_token_refresh_and_revoked_access_recovery(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    with agent_sessions() as db:
        row = db.scalars(select(GoogleCalendarConnection)).one()
        row.access_token_expires_at = row.connected_at.replace(year=2020)
        db.commit()
    _create(agent_client, "/api/meetings", {"customer_id": agent_records["customer_id"], "scheduled_at": FUTURE})
    assert ("refresh", "refresh-code-a") in google.calls and len(google.events) == 1

    with agent_sessions() as db:
        row = db.scalars(select(GoogleCalendarConnection)).one()
        row.access_token_expires_at = row.connected_at.replace(year=2020)
        db.commit()
    google.refresh_error = GoogleCalendarError("calendar_reauthorization_required", reauthorize=True)
    _create(agent_client, "/api/meetings", {"customer_id": agent_records["customer_id"], "scheduled_at": "2030-11-06T10:00:00+04:00"})
    status = _status(agent_client)
    assert (status["status"], status["failed_syncs"]) == ("reauthorization_required", 1)
    assert len(google.events) == 1

    google.refresh_error = None
    assert _connect(agent_client, "code-b").headers["location"] == "/settings?calendar=connected"
    recovered = agent_client.post("/api/integrations/google-calendar/retry").json()
    assert (recovered["status"], recovered["failed_syncs"]) == ("connected", 0)
    assert len(google.events) == 2


def test_edits_cancellations_and_disconnect(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
    google: FakeGoogle,
) -> None:
    _connect(agent_client)
    meeting = _create(
        agent_client,
        "/api/meetings",
        {"customer_id": agent_records["customer_id"], "scheduled_at": FUTURE, "agenda": "First"},
    )
    event_id = calendar_sync_service.event_id_for(1, "meeting", meeting["meeting_id"])
    path = f"/api/meetings/{meeting['meeting_id']}"

    assert agent_client.put(path, json={"agenda": "Second"}).status_code == 200
    assert google.calls[-1] == ("patch", event_id)
    assert "Agenda: Second" in google.events[event_id]["description"]

    assert agent_client.put(path, json={"status": "cancelled"}).status_code == 200
    assert google.calls[-1] == ("delete", event_id)
    assert _links(agent_sessions)[0].sync_status == "deleted"

    disconnected = agent_client.delete("/api/integrations/google-calendar").json()
    assert disconnected["connected"] is False
    assert google.revoked == ["refresh-code-a"]
    with agent_sessions() as db:
        assert db.scalars(select(GoogleCalendarConnection)).first() is None
    calls = len(google.calls)
    _create(agent_client, "/api/meetings", {"customer_id": agent_records["customer_id"], "scheduled_at": "2030-11-08T10:00:00+04:00"})
    assert len(google.calls) == calls
