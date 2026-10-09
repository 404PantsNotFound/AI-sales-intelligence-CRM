from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Call, FollowUp, Meeting, User


def test_workspace_activities_are_authenticated_and_shared(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        db.add(
            Meeting(
                customer_id=agent_records["customer_id"],
                scheduled_at=datetime(2030, 1, 10, 11, 0, tzinfo=timezone.utc),
                scheduled_timezone="Asia/Dubai",
                duration=45,
                status="scheduled",
                agenda="Quarterly review",
            )
        )
        db.add(
            FollowUp(
                customer_id=agent_records["customer_id"],
                type="Send proposal",
                due_date=datetime(2029, 12, 1, 9, 0, tzinfo=timezone.utc),
                due_timezone="Asia/Dubai",
                duration=20,
                status="overdue",
                description="Share the revised proposal.",
            )
        )
        db.commit()

    with agent_sessions() as db:
        other_user = User(
            email="dashboard.reader@example.com",
            full_name="Dashboard Reader",
            password_hash=hash_password("DashboardPassword123!"),
            role="sales",
            is_active=True,
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)
        token, _ = create_access_token(other_user)

    with TestClient(app) as other_client:
        other_client.headers["Authorization"] = f"Bearer {token}"
        response = other_client.get(
            "/api/scheduling/activities",
            params={
                "start_at": "2030-01-10T00:00:00Z",
                "end_at": "2030-01-11T00:00:00Z",
            },
        )
        earlier_range = other_client.get(
            "/api/scheduling/activities",
            params={
                "start_at": "2026-10-01T00:00:00Z",
                "end_at": "2026-10-02T00:00:00Z",
            },
        )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    meeting = next(item for item in items if item["activity_id"].startswith("meeting-"))
    assert meeting["customer_id"] == agent_records["customer_id"]
    assert meeting["customer_name"] == "ABC Trading Customer"
    assert meeting["company_name"] == "ABC Trading"
    assert meeting["activity_type"] == "meeting"
    assert meeting["timezone"] == "Asia/Dubai"
    assert meeting["duration_minutes"] == 45
    assert datetime.fromisoformat(
        meeting["starts_at"].replace("Z", "+00:00")
    ) == datetime(2030, 1, 10, 11, 0, tzinfo=timezone.utc)

    assert earlier_range.status_code == 200
    overdue = next(
        item
        for item in items
        if item["activity_type"] == "follow_up"
        and item["description"] == "Share the revised proposal."
    )
    assert overdue["status"] == "overdue"
    assert overdue["timezone"] == "Asia/Dubai"
    defaults = {
        item["activity_type"]: item["duration_minutes"]
        for item in earlier_range.json()["items"]
    }
    assert defaults == {"meeting": 60, "call": 30, "follow_up": 15}


def test_workspace_activities_reject_invalid_ranges_and_require_auth(
    agent_client: TestClient,
) -> None:
    with TestClient(app) as unauthorized:
        missing_auth = unauthorized.get(
            "/api/scheduling/activities",
            params={
                "start_at": "2030-01-11T00:00:00Z",
                "end_at": "2030-01-10T00:00:00Z",
            },
        )
    assert missing_auth.status_code == 401

    invalid_range = agent_client.get(
        "/api/scheduling/activities",
        params={
            "start_at": "2030-01-11T00:00:00Z",
            "end_at": "2030-01-10T00:00:00Z",
        },
    )
    assert invalid_range.status_code == 422


def test_moving_meeting_persists_new_day_and_keeps_saved_timezone(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        meeting.scheduled_at = datetime(2030, 5, 1, 10, 0, tzinfo=timezone.utc)
        meeting.scheduled_timezone = "Asia/Dubai"
        meeting.duration = 45
        meeting.status = "scheduled"
        db.commit()

    moved = agent_client.post(
        f"/api/scheduling/activities/meeting/{agent_records['meeting_id']}/move",
        json={"target_date": "2030-05-02", "target_timezone": "UTC"},
    )

    assert moved.status_code == 200, moved.text
    assert moved.json() == {
        "activity_id": f"meeting-{agent_records['meeting_id']}",
        "starts_at": "2030-05-02T10:00:00Z",
        "timezone": "Asia/Dubai",
    }
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        assert meeting.scheduled_at == datetime(2030, 5, 2, 10, 0)
        assert meeting.scheduled_timezone == "Asia/Dubai"

    workspace = agent_client.get(
        "/api/scheduling/activities",
        params={
            "start_at": "2030-05-02T00:00:00Z",
            "end_at": "2030-05-03T00:00:00Z",
        },
    )
    assert workspace.status_code == 200
    moved_item = next(
        item
        for item in workspace.json()["items"]
        if item["activity_id"] == f"meeting-{agent_records['meeting_id']}"
    )
    assert moved_item["timezone"] == "Asia/Dubai"
    assert moved_item["duration_minutes"] == 45


def test_move_uses_calendar_timezone_wall_clock_to_select_target_day(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        meeting.scheduled_at = datetime(2030, 5, 1, 22, 0, tzinfo=timezone.utc)
        meeting.scheduled_timezone = "Asia/Dubai"
        meeting.duration = 45
        meeting.status = "scheduled"
        db.commit()

    moved = agent_client.post(
        f"/api/scheduling/activities/meeting/{agent_records['meeting_id']}/move",
        json={
            "target_date": "2030-05-02",
            "target_timezone": "America/Los_Angeles",
        },
    )

    assert moved.status_code == 200, moved.text
    assert moved.json() == {
        "activity_id": f"meeting-{agent_records['meeting_id']}",
        "starts_at": "2030-05-02T22:00:00Z",
        "timezone": "Asia/Dubai",
    }
    moved_in_calendar_timezone = datetime.fromisoformat(
        moved.json()["starts_at"].replace("Z", "+00:00")
    ).astimezone(ZoneInfo("America/Los_Angeles"))
    assert moved_in_calendar_timezone.date().isoformat() == "2030-05-02"
    assert moved_in_calendar_timezone.hour == 15


def test_moving_activity_rejects_conflicts_and_preserves_database_time(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        meeting.scheduled_at = datetime(2030, 5, 1, 10, 0, tzinfo=timezone.utc)
        meeting.scheduled_timezone = "Asia/Dubai"
        meeting.duration = 45
        meeting.status = "scheduled"
        db.add(
            Call(
                customer_id=agent_records["customer_id"],
                scheduled_at=datetime(2030, 5, 2, 10, 15, tzinfo=timezone.utc),
                scheduled_timezone="Asia/Dubai",
                duration=30,
                status="scheduled",
                call_type="Discovery",
            )
        )
        db.commit()

    response = agent_client.post(
        f"/api/scheduling/activities/meeting/{agent_records['meeting_id']}/move",
        json={"target_date": "2030-05-02", "target_timezone": "UTC"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "schedule_conflict"
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        assert meeting.scheduled_at == datetime(2030, 5, 1, 10, 0)


def test_moving_activity_rejects_nonexistent_local_dst_time(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        meeting.scheduled_at = datetime(2026, 3, 7, 7, 30, tzinfo=timezone.utc)
        meeting.scheduled_timezone = "America/New_York"
        meeting.status = "scheduled"
        db.commit()

    response = agent_client.post(
        f"/api/scheduling/activities/meeting/{agent_records['meeting_id']}/move",
        json={"target_date": "2026-03-08", "target_timezone": "America/New_York"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_move_time"
    with agent_sessions() as db:
        meeting = db.get(Meeting, agent_records["meeting_id"])
        assert meeting is not None
        assert meeting.scheduled_at == datetime(2026, 3, 7, 7, 30)


def test_moving_calls_and_followups_updates_their_saved_schedule(
    agent_client: TestClient,
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        call = db.get(Call, agent_records["call_id"])
        followup = db.get(FollowUp, agent_records["followup_id"])
        assert call is not None
        assert followup is not None
        call.scheduled_at = datetime(2030, 5, 1, 10, 0, tzinfo=timezone.utc)
        call.scheduled_timezone = "Asia/Dubai"
        call.duration = 30
        call.status = "scheduled"
        followup.due_date = datetime(2030, 5, 1, 12, 0, tzinfo=timezone.utc)
        followup.due_timezone = "Asia/Dubai"
        followup.duration = 15
        followup.status = "pending"
        db.commit()

    moved_call = agent_client.post(
        f"/api/scheduling/activities/call/{agent_records['call_id']}/move",
        json={"target_date": "2030-05-02", "target_timezone": "Asia/Dubai"},
    )
    moved_followup = agent_client.post(
        f"/api/scheduling/activities/follow_up/{agent_records['followup_id']}/move",
        json={"target_date": "2030-05-02", "target_timezone": "Asia/Dubai"},
    )

    assert moved_call.status_code == 200, moved_call.text
    assert moved_call.json()["starts_at"] == "2030-05-02T10:00:00Z"
    assert moved_call.json()["timezone"] == "Asia/Dubai"
    assert moved_followup.status_code == 200, moved_followup.text
    assert moved_followup.json()["starts_at"] == "2030-05-02T12:00:00Z"
    assert moved_followup.json()["timezone"] == "Asia/Dubai"

    with agent_sessions() as db:
        call = db.get(Call, agent_records["call_id"])
        followup = db.get(FollowUp, agent_records["followup_id"])
        assert call is not None
        assert followup is not None
        assert call.scheduled_at == datetime(2030, 5, 2, 10, 0)
        assert call.scheduled_timezone == "Asia/Dubai"
        assert followup.due_date == datetime(2030, 5, 2, 12, 0)
        assert followup.due_timezone == "Asia/Dubai"


def test_moving_activity_requires_authentication(agent_records: dict[str, int]) -> None:
    with TestClient(app) as unauthorized:
        response = unauthorized.post(
            f"/api/scheduling/activities/meeting/{agent_records['meeting_id']}/move",
            json={"target_date": "2030-05-02", "target_timezone": "UTC"},
        )
    assert response.status_code == 401
