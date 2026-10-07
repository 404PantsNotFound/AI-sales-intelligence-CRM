from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.rate_limit import enforce_ai_rate_limit
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.agent import (
    AgentActionResult,
    AgentChatRequest,
    AgentChatResponse,
    CustomerSummaryResponse,
    MeetingBriefResponse,
)
from app.services import agent_action_service, agent_service, ai_service

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post(
    "/chat",
    response_model=AgentChatResponse,
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def chat(
    request: AgentChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentChatResponse:
    return agent_service.chat_with_agent(db, request, user_id=current_user.user_id)


@router.post(
    "/actions/{action_id}/confirm",
    response_model=AgentActionResult,
    summary="Confirm a pending CRM action",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def confirm_action(
    action_id: str = Path(min_length=1, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentActionResult:
    return agent_action_service.confirm_action(
        db,
        action_id,
        user_id=current_user.user_id,
    )


@router.post(
    "/actions/{action_id}/cancel",
    response_model=AgentActionResult,
    summary="Cancel a pending CRM action",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def cancel_action(
    action_id: str = Path(min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
) -> AgentActionResult:
    return agent_action_service.cancel_action(action_id, user_id=current_user.user_id)


@router.post(
    "/customer-summary/{customer_id}",
    response_model=CustomerSummaryResponse,
    summary="Generate an AI customer summary",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def customer_summary(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CustomerSummaryResponse:
    return ai_service.generate_customer_summary(db, customer_id)


@router.post(
    "/meeting-brief/{meeting_id}",
    response_model=MeetingBriefResponse,
    summary="Generate an AI meeting preparation brief",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def meeting_brief(
    meeting_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> MeetingBriefResponse:
    return ai_service.generate_meeting_brief(db, meeting_id)


@router.get("/test")
def test_agent() -> dict[str, str]:
    return {"status": "ok", "module": "agent"}

