import json
import logging
from typing import Any, TypeGuard
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Interrupt
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.actions import action_checkpointer
from app.agent.agent import build_sales_agent
from app.agent.model import create_chat_model, map_ai_provider_exception
from app.agent.tools import AgentToolContext
from app.agent.tools.action_schemas import (
    CompleteFollowupProposal,
    CreateFollowupProposal,
    CreateMeetingProposal,
    RecordCallResultProposal,
)
from app.core.config import settings
from app.core.exceptions import APIError
from app.core.logging_utils import contains_sensitive_material, redact_sensitive_text
from app.schemas.agent import (
    AgentActionName,
    AgentActionResult,
    AgentChatRequest,
    AgentChatResponse,
    AgentToolCall,
    PendingAgentAction,
)
from app.schemas.call import CallCreate, CallResponse
from app.schemas.follow_up import FollowUpCreate, FollowUpResponse
from app.schemas.meeting import MeetingCreate, MeetingResponse
from app.services import call_service, followup_service, meeting_service
from app.services.activity_validation import validate_customer_references
from app.services.agent_action_state import PendingActionRecord, pending_actions

logger = logging.getLogger(__name__)

WRITE_TOOL_NAMES: frozenset[AgentActionName] = frozenset(
    {
        "create_meeting",
        "create_followup",
        "record_call_result",
        "complete_followup",
    }
)


def _is_agent_action(value: object) -> TypeGuard[AgentActionName]:
    return isinstance(value, str) and value in WRITE_TOOL_NAMES


def _validate_proposal(
    action: AgentActionName,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if action == "create_meeting":
        return CreateMeetingProposal.model_validate(payload).model_dump(mode="json")
    if action == "create_followup":
        return CreateFollowupProposal.model_validate(payload).model_dump(mode="json")
    if action == "record_call_result":
        return RecordCallResultProposal.model_validate(payload).model_dump(mode="json")
    return CompleteFollowupProposal.model_validate(payload).model_dump(mode="json")


def _proposal_payload(value: Any) -> tuple[AgentActionName, dict[str, Any]]:
    if not isinstance(value, dict):
        raise APIError("The proposed action is invalid.", 502, "invalid_action_proposal")
    action = value.get("action")
    payload = value.get("payload")
    if not _is_agent_action(action) or not isinstance(payload, dict):
        raise APIError("The proposed action is invalid.", 502, "invalid_action_proposal")
    try:
        validated_payload = _validate_proposal(action, payload)
    except ValidationError as exc:
        raise APIError(
            "The proposed action could not be validated.",
            422,
            "invalid_action_proposal",
        ) from exc
    return action, validated_payload


def _validate_proposal_customer_scope(
    db: Session,
    *,
    request_customer_id: int | None,
    tool_context: AgentToolContext,
    payload: dict[str, Any],
) -> None:
    target_customer_id = payload.get("customer_id")
    if not isinstance(target_customer_id, int):
        raise APIError("The proposed action is invalid.", 422, "invalid_action_proposal")
    if request_customer_id is not None and target_customer_id != request_customer_id:
        raise APIError(
            "Write proposal cannot target a customer outside the active conversation scope.",
            403,
            "customer_scope_violation",
        )
    if not tool_context.has_customer(target_customer_id):
        raise APIError(
            "Use CRM read tools to identify this customer before proposing an action.",
            400,
            "record_not_retrieved",
        )

    contact_id = payload.get("contact_id")
    enquiry_id = payload.get("enquiry_id")
    meeting_id = payload.get("meeting_id")
    call_id = payload.get("call_id")
    followup_id = payload.get("followup_id")

    for record_id, has_fn, label in (
        (contact_id, tool_context.has_contact, "contact"),
        (enquiry_id, tool_context.has_enquiry, "enquiry"),
        (meeting_id, tool_context.has_meeting, "meeting"),
        (call_id, tool_context.has_call, "call"),
        (followup_id, tool_context.has_followup, "followup"),
    ):
        if record_id is not None and not has_fn(target_customer_id, int(record_id)):
            raise APIError(
                f"Use CRM read tools to identify this {label} before proposing an action.",
                400,
                "record_not_retrieved",
            )

    validate_customer_references(
        db,
        target_customer_id,
        contact_id=contact_id,
        enquiry_id=enquiry_id,
        meeting_id=meeting_id,
        call_id=call_id,
        followup_id=followup_id,
    )


def _extract_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        text_parts.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
                text = "\n".join(text_parts).strip()
                if text:
                    return text
    return "I could not complete that request."


def _read_tool_calls(messages: list[Any]) -> list[AgentToolCall]:
    results_by_id = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }
    calls: list[AgentToolCall] = []
    for message in messages:
        for call in getattr(message, "tool_calls", []):
            name = call.get("name", "unknown")
            if name in WRITE_TOOL_NAMES:
                continue
            result = results_by_id.get(call.get("id"))
            error_code = None
            if result is not None and isinstance(result.content, str):
                try:
                    data = json.loads(result.content)
                except json.JSONDecodeError:
                    data = {}
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict) and isinstance(error.get("code"), str):
                    error_code = error["code"]
            args = call.get("args")
            safe_args = args if isinstance(args, dict) else {}
            summary = {
                key: value
                for key, value in safe_args.items()
                if isinstance(value, (str, int))
            }
            calls.append(
                AgentToolCall(
                    name=name,
                    input_summary=summary,
                    success=error_code is None,
                    error_code=error_code,
                )
            )
    return calls


def propose_or_answer(
    db: Session,
    request: AgentChatRequest,
    *,
    model: BaseChatModel | None = None,
    user_id: int = 0,
) -> AgentChatResponse:
    tool_context = AgentToolContext(locked_customer_id=request.customer_id)
    if request.customer_id is not None:
        tool_context.remember_customer(request.customer_id)
    prompt = request.message
    if request.customer_id is not None:
        prompt = (
            f"Selected CRM customer ID (use read tools to retrieve its facts): "
            f"{request.customer_id}\nUser request: {request.message}"
        )

    thread_id = str(uuid4())
    try:
        agent = build_sales_agent(
            db,
            model=model or create_chat_model(),
            allow_actions=True,
            tool_context=tool_context,
        )
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": settings.agent_recursion_limit,
                },
            )
        finally:
            # Immediately clean up the LangGraph checkpoint thread after extracting state;
            # confirmation executes validated CRM service calls directly rather than resuming the graph.
            action_checkpointer.delete_thread(thread_id)

        interrupts = state.get("__interrupt__", [])
        if interrupts:
            if len(interrupts) != 1 or not isinstance(interrupts[0], Interrupt):
                raise APIError(
                    "Please propose one CRM action at a time.",
                    409,
                    "multiple_pending_actions",
                )
            action, payload = _proposal_payload(interrupts[0].value)
            _validate_proposal_customer_scope(
                db,
                request_customer_id=request.customer_id,
                tool_context=tool_context,
                payload=payload,
            )
            pending = pending_actions.add(
                action,
                payload,
                thread_id,
                user_id=user_id,
            )
            return AgentChatResponse(
                response=f"I am ready to {action.replace('_', ' ')}. Please confirm.",
                customer_id=request.customer_id,
                tool_calls=_read_tool_calls(state.get("messages", [])),
                pending_action=PendingAgentAction(
                    action_id=pending.action_id,
                    action=action,
                    payload=payload,
                ),
            )
        messages = state.get("messages", [])
        return AgentChatResponse(
            response=_extract_text(messages),
            customer_id=request.customer_id,
            tool_calls=_read_tool_calls(messages),
        )
    except APIError:
        raise
    except Exception as exc:
        raise map_ai_provider_exception(
            exc,
            operation="agent_chat",
            default_code="agent_error",
            default_message="The AI assistant could not complete this request.",
        ) from exc


def _execute_action(db: Session, record: PendingActionRecord) -> AgentActionResult:
    if record.action == "create_meeting":
        proposal = CreateMeetingProposal.model_validate(record.payload)
        meeting_data = MeetingCreate.model_validate(proposal.model_dump())
        meeting = meeting_service.create_meeting(db, meeting_data)
        serialized = MeetingResponse.model_validate(meeting).model_dump(mode="json")
        record_id = meeting.meeting_id
        message = "Meeting created successfully."
    elif record.action == "create_followup":
        proposal = CreateFollowupProposal.model_validate(record.payload)
        followup_data = FollowUpCreate.model_validate(proposal.model_dump())
        followup = followup_service.create_followup(db, followup_data)
        serialized = FollowUpResponse.model_validate(followup).model_dump(mode="json")
        record_id = followup.followup_id
        message = "Follow-up created successfully."
    elif record.action == "record_call_result":
        proposal = RecordCallResultProposal.model_validate(record.payload)
        call_data = CallCreate.model_validate(proposal.model_dump())
        call = call_service.create_call(db, call_data)
        serialized = CallResponse.model_validate(call).model_dump(mode="json")
        record_id = call.call_id
        message = "Call result recorded successfully. No call was placed."
    else:
        proposal = CompleteFollowupProposal.model_validate(record.payload)
        completed = followup_service.complete_customer_followup(
            db,
            customer_id=proposal.customer_id,
            followup_id=proposal.followup_id,
        )
        serialized = FollowUpResponse.model_validate(completed).model_dump(mode="json")
        record_id = completed.followup_id
        message = "Follow-up marked as completed."

    return AgentActionResult(
        status="completed",
        action=record.action,
        record_id=record_id,
        record=serialized,
        message=message,
    )


def _discard_action(action_id: str, record: PendingActionRecord) -> None:
    pending_actions.remove(action_id)
    action_checkpointer.delete_thread(record.thread_id)


def confirm_action(
    db: Session,
    action_id: str,
    *,
    user_id: int = 0,
) -> AgentActionResult:
    record = pending_actions.claim(action_id, user_id=user_id)
    try:
        try:
            return _execute_action(db, record)
        except APIError as exc:
            return AgentActionResult(
                status="failed",
                action=record.action,
                error_code=exc.code,
                message=exc.message,
            )
        except Exception as exc:
            raw_message = str(exc)
            if contains_sensitive_material(raw_message):
                logger.error(
                    "Unexpected error executing confirmed action [action=%s error_type=%s detail=%s]",
                    record.action,
                    type(exc).__name__,
                    redact_sensitive_text(raw_message),
                )
            else:
                logger.exception(
                    "Unexpected error executing confirmed action [action=%s error_type=%s]",
                    record.action,
                    type(exc).__name__,
                )
            raise APIError(
                "The CRM action could not be completed.",
                500,
                "action_execution_error",
            ) from exc
    finally:
        _discard_action(action_id, record)


def cancel_action(action_id: str, *, user_id: int = 0) -> AgentActionResult:
    record = pending_actions.claim(action_id, user_id=user_id)
    _discard_action(action_id, record)
    return AgentActionResult(
        status="cancelled",
        action=record.action,
        message="Action cancelled. No CRM changes were made.",
    )
