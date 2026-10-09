from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.exceptions import APIError
from app.core.rate_limit import enforce_ai_rate_limit
from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.agent import (
    AgentActionMode,
    AgentActionName,
    AgentActionResult,
    AgentChatRequest,
    AgentChatResponse,
    AgentPolicyUpdateRequest,
    AgentTaskInputRequest,
    AgentTaskResponse,
    AgentTaskStartRequest,
    CustomerSummaryResponse,
    MeetingBriefResponse,
)
from app.services import agent_action_service, agent_service, ai_service
from app.services.hitl_policy import get_action_policy, list_action_policies, set_action_policy
from app.services import hitl_task_service

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
        is_admin=current_user.role == "admin",
    )


@router.post(
    "/actions/{action_id}/cancel",
    response_model=AgentActionResult,
    summary="Cancel a pending CRM action",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def cancel_action(
    action_id: str = Path(min_length=1, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentActionResult:
    return agent_action_service.cancel_action(
        db,
        action_id,
        user_id=current_user.user_id,
    )


@router.post(
    "/actions/{action_id}/reject",
    response_model=AgentActionResult,
    summary="Reject a pending CRM action",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def reject_action(
    action_id: str = Path(min_length=1, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentActionResult:
    return agent_action_service.reject_action(
        db,
        action_id,
        user_id=current_user.user_id,
        is_admin=current_user.role == "admin",
    )


@router.post(
    "/tasks",
    response_model=AgentTaskResponse,
    summary="Create a structured HITL task",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def create_agent_task(
    request: AgentTaskStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentTaskResponse:
    return hitl_task_service.create_task(
        db,
        request,
        user_id=current_user.user_id,
    )


@router.get(
    "/tasks",
    response_model=list[AgentTaskResponse],
    summary="List persisted HITL tasks for the current user",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def list_agent_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AgentTaskResponse]:
    return hitl_task_service.list_tasks(
        db,
        user_id=current_user.user_id,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=AgentTaskResponse,
    summary="Resume a structured HITL task",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def resume_agent_task(
    task_id: str = Path(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentTaskResponse:
    return hitl_task_service.resume_task(
        db,
        task_id,
        user_id=current_user.user_id,
    )


@router.post(
    "/tasks/{task_id}/inputs",
    response_model=AgentTaskResponse,
    summary="Submit structured HITL task information",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def submit_agent_task_input(
    request: AgentTaskInputRequest,
    task_id: str = Path(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentTaskResponse:
    return hitl_task_service.submit_task_input(
        db,
        task_id,
        request,
        user_id=current_user.user_id,
    )


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=AgentTaskResponse,
    summary="Cancel a HITL task",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def cancel_agent_task(
    task_id: str = Path(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentTaskResponse:
    return hitl_task_service.cancel_task(
        db,
        task_id,
        user_id=current_user.user_id,
    )


@router.get(
    "/policies",
    summary="List current action autonomy policies",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def list_agent_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, AgentActionMode]:
    if current_user.role != "admin":
        raise APIError("Only administrators can view autonomy policy settings.", 403, "forbidden")
    return list_action_policies(db)


@router.put(
    "/policies/{action}",
    summary="Update an action autonomy policy",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def update_agent_policy(
    action: AgentActionName,
    payload: AgentPolicyUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    if current_user.role != "admin":
        raise APIError("Only administrators can modify autonomy policy settings.", 403, "forbidden")
    if payload.action != action:
        raise APIError(
            "The action in the request body must match the action in the URL.",
            422,
            "policy_action_mismatch",
        )
    return set_action_policy(
        db,
        action,
        payload.mode,
        changed_by=current_user.email,
    )


@router.api_route(
    "/customer-summary/{customer_id}",
    methods=["GET", "POST"],
    response_model=CustomerSummaryResponse,
    summary="Generate an AI customer summary",
    dependencies=[Depends(enforce_ai_rate_limit)],
)
def customer_summary(
    customer_id: int = Path(ge=1),
    db: Session = Depends(get_db),
) -> CustomerSummaryResponse:
    return ai_service.generate_customer_summary(db, customer_id)


@router.api_route(
    "/meeting-brief/{meeting_id}",
    methods=["GET", "POST"],
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
