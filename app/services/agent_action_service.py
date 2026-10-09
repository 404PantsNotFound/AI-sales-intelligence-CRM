import json
import logging
from datetime import datetime, timezone
from typing import Any, TypeGuard
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Interrupt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.actions import action_checkpointer
from app.agent.agent import build_sales_agent
from app.agent.model import create_chat_model, map_ai_provider_exception
from app.agent.tools import AgentToolContext
from app.agent.tools.action_schemas import (
    ApplyEnrichmentProposal,
    CompleteFollowupProposal,
    CreateFollowupProposal,
    CreateMeetingProposal,
    RecordCallResultProposal,
    ScheduleCallProposal,
)
from app.core.config import settings
from app.core.exceptions import APIError
from app.database.connection import atomic_transaction
from app.core.logging_utils import (
    contains_sensitive_material,
    redact_sensitive_text,
)
from app.schemas.agent import (
    AgentActionName,
    AgentActionResult,
    AgentChatRequest,
    AgentChatResponse,
    AgentTaskResponse,
    AgentToolCall,
    PendingAgentAction,
)
from app.schemas.call import CallCreate, CallResponse
from app.schemas.customer import CustomerResponse
from app.schemas.follow_up import FollowUpCreate, FollowUpResponse
from app.schemas.meeting import MeetingCreate, MeetingResponse
from app.schemas.sales_enquiry import SalesEnquiryResponse
from app.models import HitlActionProposal, HitlExecutionAudit, User
from app.services import (
    call_service,
    enrichment_service,
    followup_service,
    meeting_service,
)
from app.services.activity_validation import validate_customer_references
from app.services.scheduling_service import lock_workspace_schedule
from app.services.agent_action_state import (
    PendingActionRecord,
    action_fingerprint,
    pending_actions,
)
from app.services.hitl_policy import enforce_action_policy, get_action_policy
from app.services.hitl_action_registry import validate_operation

logger = logging.getLogger(__name__)

WRITE_TOOL_NAMES: frozenset[AgentActionName] = frozenset(
    {
        "create_meeting",
        "create_followup",
        "schedule_call",
        "record_call_result",
        "complete_followup",
        "apply_enrichment",
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

    if action == "schedule_call":
        return ScheduleCallProposal.model_validate(payload).model_dump(mode="json")

    if action == "record_call_result":
        return RecordCallResultProposal.model_validate(payload).model_dump(mode="json")

    if action == "apply_enrichment":
        return ApplyEnrichmentProposal.model_validate(payload).model_dump(mode="json")

    return CompleteFollowupProposal.model_validate(payload).model_dump(mode="json")


def _proposal_payload(value: Any) -> tuple[AgentActionName, dict[str, Any]]:
    if not isinstance(value, dict):
        raise APIError(
            "The proposed action is invalid.",
            502,
            "invalid_action_proposal",
        )

    action = value.get("action")
    payload = value.get("payload")

    if not _is_agent_action(action) or not isinstance(payload, dict):
        raise APIError(
            "The proposed action is invalid.",
            502,
            "invalid_action_proposal",
        )

    try:
        validated_payload = _validate_proposal(action, payload)
    except ValidationError as exc:
        raise APIError(
            "The proposed action could not be validated.",
            422,
            "invalid_action_proposal",
        ) from exc

    return action, validated_payload


def _meeting_clarification_question(missing_fields: list[str]) -> str:
    missing = set(missing_fields)
    if "valid_scheduled_time" in missing:
        return (
            "That local time does not exist because of a daylight-saving change. "
            "What valid time should I use?"
        )
    if "scheduled_time_occurrence" in missing:
        return (
            "That local time occurs twice because of a daylight-saving change. "
            "Should I use the earlier or later occurrence?"
        )
    date_missing = "scheduled_date" in missing
    time_missing = "scheduled_time" in missing
    timezone_missing = "scheduled_timezone" in missing
    if date_missing and time_missing:
        question = "Sure — what date and time would you like to schedule the meeting?"
    elif date_missing:
        question = "What date would you like to schedule the meeting?"
    elif time_missing:
        question = "What time would you like to schedule the meeting?"
    else:
        question = "What timezone should I use for the meeting?"
    if timezone_missing:
        question = question[:-1] + ", and what timezone should I use?"
    return question


def _validate_proposal_customer_scope(
    db: Session,
    *,
    request_customer_id: int | None,
    tool_context: AgentToolContext,
    payload: dict[str, Any],
) -> None:
    target_customer_id = payload.get("customer_id")

    if not isinstance(target_customer_id, int):
        raise APIError(
            "The proposed action is invalid.",
            422,
            "invalid_action_proposal",
        )

    if (
        request_customer_id is not None
        and target_customer_id != request_customer_id
    ):
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
        if record_id is not None and not has_fn(
            target_customer_id,
            int(record_id),
        ):
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
                    elif isinstance(item, dict) and isinstance(
                        item.get("text"),
                        str,
                    ):
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

                if isinstance(error, dict) and isinstance(
                    error.get("code"),
                    str,
                ):
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
    tool_context = AgentToolContext(
        locked_customer_id=request.customer_id,
        timezone_name=request.timezone_name,
    )

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
            logger.info(
                "Starting agent request [thread_id=%s customer_id=%s message=%s]",
                thread_id,
                request.customer_id,
                request.message,
            )

            state = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ]
                },
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": settings.agent_recursion_limit,
                },
            )

            logger.info(
                "Agent completed [thread_id=%s messages=%s interrupts=%s]",
                thread_id,
                len(state.get("messages", [])),
                len(state.get("__interrupt__", [])),
            )
        finally:
            # Immediately clean up the LangGraph checkpoint thread after
            # extracting state; confirmation executes validated CRM service
            # calls directly rather than resuming the graph.
            action_checkpointer.delete_thread(thread_id)

        interrupts = state.get("__interrupt__", [])

        if interrupts:
            if (
                len(interrupts) != 1
                or not isinstance(interrupts[0], Interrupt)
            ):
                raise APIError(
                    "Please propose one CRM action at a time.",
                    409,
                    "multiple_pending_actions",
                )

            interrupt_value = interrupts[0].value
            if (
                isinstance(interrupt_value, dict)
                and interrupt_value.get("clarification") is True
            ):
                action = interrupt_value.get("action")
                payload = interrupt_value.get("payload")
                if action != "create_meeting" or not isinstance(payload, dict):
                    raise APIError(
                        "The meeting clarification request is invalid.",
                        502,
                        "invalid_action_proposal",
                    )
                _validate_proposal_customer_scope(
                    db,
                    request_customer_id=request.customer_id,
                    tool_context=tool_context,
                    payload=payload,
                )
                enforce_action_policy(db, action)
                _, missing_fields = validate_operation(action, payload)
                if not missing_fields:
                    raise APIError(
                        "A complete meeting must use the approval proposal flow.",
                        502,
                        "invalid_action_proposal",
                    )
                task = pending_actions.create_task(
                    db,
                    user_id=user_id,
                    action_type=action,
                    collected_data={
                        "operations": [
                            {
                                "action": action,
                                "values": payload,
                                "status": "collecting_information",
                            }
                        ]
                    },
                )
                from app.services.hitl_task_service import _response as build_task_response

                clarification_task: AgentTaskResponse = build_task_response(db, task)
                return AgentChatResponse(
                    response=_meeting_clarification_question(missing_fields),
                    customer_id=request.customer_id,
                    tool_calls=_read_tool_calls(state.get("messages", [])),
                    clarification_task=clarification_task,
                )

            action, payload = _proposal_payload(interrupt_value)

            _validate_proposal_customer_scope(
                db,
                request_customer_id=request.customer_id,
                tool_context=tool_context,
                payload=payload,
            )

            enforce_action_policy(db, action)
            if (
                action != "create_meeting"
                and get_action_policy(db, action) == "automatic"
            ):
                pending = pending_actions.add(
                    db,
                    action,
                    payload,
                    thread_id,
                    user_id=user_id,
                    start_execution=True,
                )
                result = _run_claimed_action(
                    db,
                    pending,
                    require_automatic=True,
                )
                return AgentChatResponse(
                    response=result.message,
                    customer_id=request.customer_id,
                    tool_calls=_read_tool_calls(state.get("messages", [])),
                )

            pending = pending_actions.add(
                db,
                action,
                payload,
                thread_id,
                user_id=user_id,
            )

            return AgentChatResponse(
                response=(
                    f"I am ready to {action.replace('_', ' ')}. "
                    "Please confirm."
                ),
                customer_id=request.customer_id,
                tool_calls=_read_tool_calls(
                    state.get("messages", [])
                ),
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


def _execute_action(
    db: Session,
    record: PendingActionRecord,
    *,
    require_automatic: bool = False,
) -> AgentActionResult:
    proposal_row = db.scalar(
        select(HitlActionProposal)
        .where(HitlActionProposal.action_id == record.action_id)
        .with_for_update()
    )
    actor = db.get(User, record.user_id)
    if proposal_row is None or actor is None or not actor.is_active:
        raise APIError(
            "The action owner is no longer authorized to execute this action.",
            403,
            "forbidden",
        )
    owner = db.get(User, proposal_row.owner_user_id)
    if owner is None or not owner.is_active:
        raise APIError(
            "The action owner is no longer authorized to execute this action.",
            403,
            "forbidden",
        )
    if proposal_row.owner_user_id != record.user_id and actor.role != "admin":
        raise APIError(
            "You are not authorized to execute this pending action.",
            403,
            "forbidden",
        )
    expires_at = proposal_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise APIError("Pending action has expired.", 410, "action_expired")
    if (
        proposal_row.status != "executing"
        or proposal_row.action_type != record.action
        or proposal_row.payload_fingerprint != record.payload_fingerprint
        or action_fingerprint(
            proposal_row.action_type,
            proposal_row.parameters,
        )
        != record.payload_fingerprint
    ):
        raise APIError(
            "The action parameters or state changed and can no longer be executed.",
            409,
            "action_parameters_changed",
        )

    policy = get_action_policy(db, record.action, for_update=True)
    if policy == "disabled":
        raise APIError(
            "This action is disabled by the global autonomy policy and cannot execute.",
            403,
            "action_disabled",
        )
    if require_automatic and policy != "automatic":
        raise APIError(
            "The action policy changed before automatic execution could begin.",
            409,
            "action_policy_changed",
        )
    if action_fingerprint(record.action, record.payload) != record.payload_fingerprint:
        raise APIError(
            "The approved action parameters have changed and can no longer be executed.",
            409,
            "action_parameters_changed",
        )
    try:
        validated_payload = _validate_proposal(record.action, record.payload)
    except ValidationError as exc:
        raise APIError(
            "The action parameters are no longer valid.",
            422,
            "invalid_action_proposal",
        ) from exc
    if action_fingerprint(record.action, validated_payload) != record.payload_fingerprint:
        raise APIError(
            "The approved action parameters have changed and can no longer be executed.",
            409,
            "action_parameters_changed",
        )
    record = PendingActionRecord(
        action_id=record.action_id,
        action=record.action,
        payload=validated_payload,
        task_id=record.task_id,
        expires_at=record.expires_at,
        user_id=record.user_id,
        payload_fingerprint=record.payload_fingerprint,
    )

    customer_id = record.payload.get("customer_id")
    if not isinstance(customer_id, int):
        raise APIError("The action target is invalid.", 422, "invalid_action_proposal")
    if record.action in {"create_meeting", "create_followup", "schedule_call"}:
        lock_workspace_schedule(db)
    validate_customer_references(
        db,
        customer_id,
        contact_id=record.payload.get("contact_id"),
        enquiry_id=record.payload.get("enquiry_id"),
        meeting_id=record.payload.get("meeting_id"),
        call_id=record.payload.get("call_id"),
        followup_id=record.payload.get("followup_id"),
    )
    if record.action == "schedule_call":
        proposal = ScheduleCallProposal.model_validate(record.payload)
        call_data = CallCreate.model_validate(proposal.model_dump())
        call = call_service.create_call(
            db,
            call_data,
            defer_commit=True,
        )
        serialized = CallResponse.model_validate(call).model_dump(mode="json")
        record_id = call.call_id
        message = "Scheduled call created successfully."

    elif record.action == "create_meeting":
        proposal = CreateMeetingProposal.model_validate(record.payload)

        meeting_data = MeetingCreate.model_validate(
            proposal.model_dump()
        )

        meeting = meeting_service.create_meeting(
            db,
            meeting_data,
            defer_commit=True,
        )

        serialized = MeetingResponse.model_validate(
            meeting
        ).model_dump(mode="json")

        record_id = meeting.meeting_id
        message = "Meeting created successfully."

    elif record.action == "create_followup":
        proposal = CreateFollowupProposal.model_validate(record.payload)

        followup_data = FollowUpCreate.model_validate(
            proposal.model_dump()
        )

        followup = followup_service.create_followup(
            db,
            followup_data,
            defer_commit=True,
        )

        serialized = FollowUpResponse.model_validate(
            followup
        ).model_dump(mode="json")

        record_id = followup.followup_id
        message = "Follow-up created successfully."

    elif record.action == "record_call_result":
        proposal = RecordCallResultProposal.model_validate(record.payload)

        call_data = CallCreate.model_validate(
            proposal.model_dump()
        )

        call = call_service.create_call(
            db,
            call_data,
            defer_commit=True,
        )

        serialized = CallResponse.model_validate(
            call
        ).model_dump(mode="json")

        record_id = call.call_id
        message = "Call result recorded successfully. No call was placed."

    elif record.action == "apply_enrichment":
        proposal = ApplyEnrichmentProposal.model_validate(
            record.payload
        )

        customer_updates: dict[str, object] = {}
        enquiry_updates: dict[str, object] = {}

        if proposal.sales_stage is not None:
            customer_updates["sales_stage"] = proposal.sales_stage

        if proposal.customer_status is not None:
            customer_updates["status"] = proposal.customer_status

        if proposal.enquiry_priority is not None:
            enquiry_updates["priority"] = proposal.enquiry_priority

        if proposal.enquiry_status is not None:
            enquiry_updates["status"] = proposal.enquiry_status

        if proposal.estimated_value is not None:
            enquiry_updates["estimated_value"] = proposal.estimated_value

        updated_customer, updated_enquiry = (
            enrichment_service.apply_enrichment(
                db,
                customer_id=proposal.customer_id,
                enquiry_id=proposal.enquiry_id,
                customer_updates=customer_updates,
                enquiry_updates=enquiry_updates,
                defer_commit=True,
            )
        )

        serialized: dict[str, Any] = {}

        if updated_customer is not None:
            serialized["customer"] = CustomerResponse.model_validate(
                updated_customer
            ).model_dump(mode="json")

        if updated_enquiry is not None:
            serialized["enquiry"] = SalesEnquiryResponse.model_validate(
                updated_enquiry
            ).model_dump(mode="json")

        record_id = proposal.customer_id
        message = "CRM enrichment applied successfully."

    else:
        proposal = CompleteFollowupProposal.model_validate(
            record.payload
        )

        completed = followup_service.complete_customer_followup(
            db,
            customer_id=proposal.customer_id,
            followup_id=proposal.followup_id,
            defer_commit=True,
        )

        serialized = FollowUpResponse.model_validate(
            completed
        ).model_dump(mode="json")

        record_id = completed.followup_id
        message = "Follow-up marked as completed."

    return AgentActionResult(
        status="completed",
        action=record.action,
        record_id=record_id,
        record=serialized,
        message=message,
    )


def confirm_action(
    db: Session,
    action_id: str,
    *,
    user_id: int = 0,
    is_admin: bool = False,
) -> AgentActionResult:
    proposal = db.get(HitlActionProposal, action_id)
    if (
        proposal is not None
        and proposal.status == "pending"
        and (proposal.owner_user_id == user_id or is_admin)
        and _is_agent_action(proposal.action_type)
        and get_action_policy(db, proposal.action_type) == "disabled"
    ):
        pending_actions.block_disabled(
            db,
            action_id,
            user_id=user_id,
            is_admin=is_admin,
        )
        raise APIError(
            "This action is disabled by the global autonomy policy and cannot execute.",
            403,
            "action_disabled",
        )

    record = pending_actions.claim(
        db,
        action_id,
        user_id=user_id,
        is_admin=is_admin,
    )

    return _run_claimed_action(db, record)


def _action_failure_message(error: APIError) -> str:
    if error.code != "schedule_conflict" or error.details is None:
        return error.message
    suggestions = error.details.get("suggestions")
    if not isinstance(suggestions, list):
        return error.message
    alternatives = [
        f"{item['starts_at']} ({item['timezone']})"
        for item in suggestions
        if isinstance(item, dict)
        and isinstance(item.get("starts_at"), str)
        and isinstance(item.get("timezone"), str)
    ]
    if not alternatives:
        return error.message
    return f"{error.message} Available alternatives: {'; '.join(alternatives)}."


def _run_claimed_action(
    db: Session,
    record: PendingActionRecord,
    *,
    require_automatic: bool = False,
) -> AgentActionResult:
    result: AgentActionResult | None = None
    try:
        with atomic_transaction(db):
            result = _execute_action(
                db,
                record,
                require_automatic=require_automatic,
            )
            pending_actions.finish(
                db,
                record,
                status=result.status,
                result_record_id=result.record_id,
                error_code=result.error_code,
                result_message=result.message,
                commit=False,
            )
        return result
    except APIError as exc:
        db.rollback()
        result = AgentActionResult(
            status="failed",
            action=record.action,
            error_code=exc.code,
            message=_action_failure_message(exc),
            details=exc.details,
        )
        pending_actions.finish(
            db,
            record,
            status="failed",
            result_record_id=None,
            error_code=result.error_code,
            result_message=result.message,
        )
        return result
    except Exception as exc:
        db.rollback()
        proposal = db.get(HitlActionProposal, record.action_id)
        execution = db.scalar(
            select(HitlExecutionAudit).where(
                HitlExecutionAudit.action_id == record.action_id
            )
        )
        if (
            proposal is not None
            and proposal.status == "completed"
            and execution is not None
            and execution.status == "completed"
        ):
            return AgentActionResult(
                status="completed",
                action=record.action,
                record_id=execution.result_record_id,
                record=result.record if result is not None else None,
                message=execution.result_message or "The CRM action completed.",
            )

        raw_message = str(exc)

        if contains_sensitive_material(raw_message):
            logger.error(
                "Unexpected error executing confirmed action "
                "[action=%s error_type=%s detail=%s]",
                record.action,
                type(exc).__name__,
                redact_sensitive_text(raw_message),
            )
        else:
            logger.exception(
                "Unexpected error executing confirmed action "
                "[action=%s error_type=%s]",
                record.action,
                type(exc).__name__,
            )

        result = AgentActionResult(
            status="failed",
            action=record.action,
            error_code="action_execution_error",
            message="The CRM action could not be completed.",
        )
        pending_actions.finish(
            db,
            record,
            status="failed",
            result_record_id=None,
            error_code=result.error_code,
            result_message=result.message,
        )
        return result


def cancel_action(
    db: Session,
    action_id: str,
    *,
    user_id: int = 0,
) -> AgentActionResult:
    record = pending_actions.cancel(db, action_id, user_id=user_id)

    return AgentActionResult(
        status="cancelled",
        action=record.action,
        message="Action cancelled. No CRM changes were made.",
    )


def reject_action(
    db: Session,
    action_id: str,
    *,
    user_id: int = 0,
    is_admin: bool = False,
) -> AgentActionResult:
    record = pending_actions.reject(
        db,
        action_id,
        user_id=user_id,
        is_admin=is_admin,
    )
    return AgentActionResult(
        status="rejected",
        action=record.action,
        message="Action rejected. No CRM changes were made.",
    )