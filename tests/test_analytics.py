import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.tools import build_read_only_tools
from app.models import Company, Customer, FollowUp, SalesEnquiry
from app.schemas.analytics import AnalyticsDateRange
from app.services import analytics_service


def _add_customer(
    db: Session,
    *,
    company_name: str,
    customer_name: str,
    status: str,
    sales_stage: str,
    industry: str | None,
    created_at: datetime,
) -> Customer:
    company = Company(company_name=company_name, industry=industry)
    db.add(company)
    db.flush()
    customer = Customer(
        company=company,
        customer_name=customer_name,
        status=status,
        sales_stage=sales_stage,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(customer)
    db.flush()
    return customer


def test_overview_metrics_counts_crm_records(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        _add_customer(
            db,
            company_name="Inactive Company",
            customer_name="Inactive Customer",
            status="inactive",
            sales_stage="lost",
            industry="Technology",
            created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )
        _add_customer(
            db,
            company_name="Prospect Company",
            customer_name="Prospect Customer",
            status="prospect",
            sales_stage="new",
            industry=None,
            created_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
        )
        db.commit()
        metrics = analytics_service.get_overview_metrics(
            db,
            date(2026, 10, 1),
            date(2026, 10, 31),
        )

    assert metrics.total_customers == 3
    assert metrics.active_customers == 1
    assert metrics.inactive_customers == 1
    assert metrics.prospects == 1
    assert metrics.new_customers == 3
    assert metrics.open_enquiries == 1
    assert metrics.meetings_this_month == 1
    assert metrics.calls_this_month == 1
    assert metrics.overdue_followups == 1
    assert agent_records["customer_id"] > 0


def test_customer_and_enquiry_distributions_and_value(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        _add_customer(
            db,
            company_name="Other Company",
            customer_name="Other Customer",
            status="prospect",
            sales_stage="proposal",
            industry="Technology",
            created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )
        db.add(
            SalesEnquiry(
                customer_id=agent_records["customer_id"],
                product="Analytics",
                enquiry_text="Interested in analytics",
                priority="high",
                status="in_progress",
                estimated_value=Decimal("250.25"),
                created_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                updated_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
            )
        )
        db.commit()
        customers = analytics_service.get_customer_metrics(
            db, date(2026, 10, 1), date(2026, 10, 31), "monthly"
        )
        enquiries = analytics_service.get_enquiry_metrics(
            db, date(2026, 10, 1), date(2026, 10, 31), "weekly"
        )

    assert {item.label: item.count for item in customers.by_status} == {
        "active": 1,
        "prospect": 1,
    }
    assert {item.label: item.count for item in customers.by_sales_stage} == {
        "qualified": 1,
        "proposal": 1,
    }
    assert {item.label: item.count for item in customers.by_industry} == {
        "Retail": 1,
        "Technology": 1,
    }
    assert [(item.period, item.count) for item in customers.created_over_time] == [
        ("2026-10", 2)
    ]
    assert {item.label: item.count for item in enquiries.by_status} == {
        "open": 1,
        "in_progress": 1,
    }
    assert {item.label: item.count for item in enquiries.by_product} == {
        "CRM": 1,
        "Analytics": 1,
    }
    assert {item.label: item.count for item in enquiries.by_priority} == {
        "high": 1,
        "normal": 1,
    }
    assert enquiries.total_estimated_value == Decimal("250.25")
    assert enquiries.time_grain == "weekly"
    assert enquiries.over_time[0].period.startswith("2026-W")


def test_pipeline_and_activity_analytics_use_linked_crm_data(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    with agent_sessions() as db:
        pipeline = analytics_service.get_pipeline_metrics(
            db, date(2026, 10, 1), date(2026, 10, 31)
        )
        activities = analytics_service.get_activity_metrics(
            db, date(2026, 10, 1), date(2026, 10, 31), "monthly"
        )

    stages = {item.label: item for item in pipeline.by_sales_stage}
    assert stages["qualified"].count == 1
    assert stages["qualified"].value == Decimal("0.00")
    assert pipeline.enquiry_to_meeting_conversion_percent == Decimal("100.00")
    assert pipeline.meeting_to_proposal_conversion_percent is None
    assert pipeline.meeting_to_proposal_conversion_unavailable_reason
    assert activities.over_time[0].period == "2026-10"
    assert activities.over_time[0].meetings == 1
    assert activities.over_time[0].calls == 1
    assert activities.over_time[0].followups == 1
    assert {item.label: item.count for item in activities.meetings_by_status} == {
        "scheduled": 1
    }


def test_followup_due_windows_and_statuses(
    agent_sessions: sessionmaker[Session],
    agent_records: dict[str, int],
) -> None:
    now = datetime.now(timezone.utc)
    today = now.date()
    with agent_sessions() as db:
        previous = db.get(FollowUp, agent_records["followup_id"])
        assert previous is not None
        previous.due_date = now - timedelta(days=1)
        db.add_all(
            [
                FollowUp(
                    customer_id=agent_records["customer_id"],
                    type="call",
                    due_date=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
                    + timedelta(hours=12),
                    status="pending",
                ),
                FollowUp(
                    customer_id=agent_records["customer_id"],
                    type="email",
                    due_date=now + timedelta(days=3),
                    status="in_progress",
                ),
                FollowUp(
                    customer_id=agent_records["customer_id"],
                    type="email",
                    due_date=now + timedelta(days=2),
                    status="cancelled",
                ),
                FollowUp(
                    customer_id=agent_records["customer_id"],
                    type="email",
                    due_date=now + timedelta(days=2),
                    status="completed",
                ),
            ]
        )
        db.commit()
        metrics = analytics_service.get_followup_metrics(db)

    assert metrics.pending == 2
    assert metrics.overdue == 1
    assert metrics.completed == 1
    assert metrics.cancelled == 1
    assert metrics.due_today == 1
    assert metrics.due_next_7_days == 1
    assert {item.label: item.count for item in metrics.by_status} == {
        "pending": 2,
        "completed": 1,
        "overdue": 1,
        "cancelled": 1,
    }


def test_empty_metrics_date_validation_and_api(
    agent_sessions: sessionmaker[Session],
    agent_client: TestClient,
) -> None:
    with pytest.raises(ValidationError):
        AnalyticsDateRange(start_date=date(2026, 10, 2), end_date=date(2026, 10, 1))

    with agent_sessions() as db:
        empty = analytics_service.get_enquiry_metrics(db)
        overview = analytics_service.get_overview_metrics(db)
    assert empty.by_status == []
    assert empty.total_estimated_value == Decimal("0")
    assert overview.total_customers == 0

    overview_response = agent_client.get("/api/analytics/overview")
    assert overview_response.status_code == 200
    assert overview_response.json()["total_customers"] == 0
    for endpoint in ("customers", "enquiries", "activities", "pipeline", "followups"):
        response = agent_client.get(f"/api/analytics/{endpoint}?start_date=2026-10-01&end_date=2026-10-31")
        assert response.status_code == 200, response.text
    invalid_range = agent_client.get(
        "/api/analytics/customers?start_date=2026-10-02&end_date=2026-10-01"
    )
    assert invalid_range.status_code == 422


def test_read_only_agent_analytics_tool_returns_calculated_metrics(
    agent_sessions: sessionmaker[Session],
) -> None:
    with agent_sessions() as db:
        tools = {tool.name: tool for tool in build_read_only_tools(db)}
        output = json.loads(tools["get_sales_analytics"].invoke({}))

    assert output["overview"]["total_customers"] == 0
    assert output["pipeline"]["by_sales_stage"] == []
    assert Decimal(output["enquiries"]["total_estimated_value"]) == Decimal("0")
