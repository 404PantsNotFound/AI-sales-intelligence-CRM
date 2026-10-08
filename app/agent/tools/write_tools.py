from datetime import datetime
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from app.schemas.agent import AgentActionName
from app.schemas.follow_up import FollowUpStatus
from app.schemas.meeting import MeetingStatus

from .action_schemas import (
    ApplyEnrichmentProposal,
    CompleteFollowupProposal,
    CreateFollowupProposal,
    CreateMeetingProposal,
    RecordCallResultProposal,
)
from .context import AgentToolContext


def build_action_proposal_tools(
    context: AgentToolContext,
) -> list[StructuredTool]:
    def _prepare(
        action: AgentActionName,
        payload: dict[str, Any],
    ) -> str:
        interrupt({"action": action, "payload": payload})
        return "The action still requires confirmation through the confirmation endpoint."

    def create_meeting(
        customer_id: int,
        scheduled_at: datetime,
        contact_id: int | None = None,
        enquiry_id: int | None = None,
        duration: int | None = 60,
        status: MeetingStatus = "scheduled",
        agenda: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Propose a CRM meeting. This tool only requests explicit confirmation; it never creates the meeting."""
        data = CreateMeetingProposal.model_validate(
            {
                "customer_id": customer_id,
                "scheduled_at": scheduled_at,
                "contact_id": contact_id,
                "enquiry_id": enquiry_id,
                "duration": duration,
                "status": status,
                "agenda": agenda,
                "notes": notes,
            }
        )

        invalid = _validate_known_references(
            context,
            customer_id=data.customer_id,
            contact_id=data.contact_id,
            enquiry_id=data.enquiry_id,
        )

        if invalid:
            return invalid

        return _prepare(
            "create_meeting",
            data.model_dump(mode="json"),
        )

    def create_followup(
        customer_id: int,
        type: str,
        due_date: datetime,
        enquiry_id: int | None = None,
        meeting_id: int | None = None,
        call_id: int | None = None,
        status: FollowUpStatus = "pending",
        description: str | None = None,
        assigned_to: str | None = None,
        completed_at: datetime | None = None,
    ) -> str:
        """Propose a CRM follow-up. This tool only requests explicit confirmation; it never creates the follow-up."""
        data = CreateFollowupProposal.model_validate(
            {
                "customer_id": customer_id,
                "type": type,
                "due_date": due_date,
                "enquiry_id": enquiry_id,
                "meeting_id": meeting_id,
                "call_id": call_id,
                "status": status,
                "description": description,
                "assigned_to": assigned_to,
                "completed_at": completed_at,
            }
        )

        invalid = _validate_known_references(
            context,
            customer_id=data.customer_id,
            enquiry_id=data.enquiry_id,
            meeting_id=data.meeting_id,
            call_id=data.call_id,
        )

        if invalid:
            return invalid

        return _prepare(
            "create_followup",
            data.model_dump(mode="json"),
        )

    def record_call_result(
        customer_id: int,
        contact_id: int | None = None,
        enquiry_id: int | None = None,
        call_type: str | None = None,
        actual_time: datetime | None = None,
        status: Literal["completed"] = "completed",
        outcome: str | None = None,
        notes: str | None = None,
        summary: str | None = None,
        next_followup_date: datetime | None = None,
    ) -> str:
        """Propose recording an already completed call. It never places a call or writes before confirmation."""
        data = RecordCallResultProposal.model_validate(
            {
                "customer_id": customer_id,
                "contact_id": contact_id,
                "enquiry_id": enquiry_id,
                "call_type": call_type,
                "actual_time": actual_time,
                "status": status,
                "outcome": outcome,
                "notes": notes,
                "summary": summary,
                "next_followup_date": next_followup_date,
            }
        )

        invalid = _validate_known_references(
            context,
            customer_id=data.customer_id,
            contact_id=data.contact_id,
            enquiry_id=data.enquiry_id,
        )

        if invalid:
            return invalid

        return _prepare(
            "record_call_result",
            data.model_dump(mode="json"),
        )

    def apply_enrichment(
        customer_id: int,
        enquiry_id: int | None = None,
        sales_stage: str | None = None,
        customer_status: str | None = None,
        enquiry_priority: str | None = None,
        enquiry_status: str | None = None,
        estimated_value: float | None = None,
    ) -> str:
        """Propose applying AI-generated CRM enrichment. It never updates the CRM before explicit confirmation."""
        data = ApplyEnrichmentProposal.model_validate(
            {
                "customer_id": customer_id,
                "enquiry_id": enquiry_id,
                "sales_stage": sales_stage,
                "customer_status": customer_status,
                "enquiry_priority": enquiry_priority,
                "enquiry_status": enquiry_status,
                "estimated_value": estimated_value,
            }
        )

        invalid = _validate_known_references(
            context,
            customer_id=data.customer_id,
            enquiry_id=data.enquiry_id,
        )

        if invalid:
            return invalid

        if all(
            value is None
            for value in (
                data.sales_stage,
                data.customer_status,
                data.enquiry_priority,
                data.enquiry_status,
                data.estimated_value,
            )
        ):
            return (
                '{"error":{"code":"empty_enrichment",'
                '"message":"No CRM changes were proposed."}}'
            )

        return _prepare(
            "apply_enrichment",
            data.model_dump(mode="json"),
        )

    def complete_followup(
        customer_id: int,
        followup_id: int,
    ) -> str:
        """Propose marking a retrieved follow-up completed; explicit confirmation is still required."""
        data = CompleteFollowupProposal.model_validate(
            {
                "customer_id": customer_id,
                "followup_id": followup_id,
            }
        )

        if not context.can_access_customer(data.customer_id):
            return _scope_violation()

        if not context.has_customer(data.customer_id):
            return _unknown_reference("customer")

        if not context.has_followup(
            data.customer_id,
            data.followup_id,
        ):
            return _unknown_reference("follow-up")

        return _prepare(
            "complete_followup",
            data.model_dump(mode="json"),
        )

    return [
        StructuredTool.from_function(
            func=apply_enrichment,
            name="apply_enrichment",
            description=apply_enrichment.__doc__,
            args_schema=ApplyEnrichmentProposal,
        ),
        StructuredTool.from_function(
            func=create_meeting,
            name="create_meeting",
            description=create_meeting.__doc__,
            args_schema=CreateMeetingProposal,
        ),
        StructuredTool.from_function(
            func=create_followup,
            name="create_followup",
            description=create_followup.__doc__,
            args_schema=CreateFollowupProposal,
        ),
        StructuredTool.from_function(
            func=record_call_result,
            name="record_call_result",
            description=record_call_result.__doc__,
            args_schema=RecordCallResultProposal,
        ),
        StructuredTool.from_function(
            func=complete_followup,
            name="complete_followup",
            description=complete_followup.__doc__,
            args_schema=CompleteFollowupProposal,
        ),
    ]


def _scope_violation() -> str:
    return (
        '{"error":{"code":"customer_scope_violation",'
        '"message":"This conversation is locked to the selected customer."}}'
    )


def _unknown_reference(kind: str) -> str:
    return (
        '{"error":{"code":"record_not_retrieved",'
        f'"message":"Use CRM read tools to identify this {kind} before proposing an action."'
        + "}}"
    )


def _validate_known_references(
    context: AgentToolContext,
    *,
    customer_id: int,
    contact_id: int | None = None,
    enquiry_id: int | None = None,
    meeting_id: int | None = None,
    call_id: int | None = None,
) -> str | None:
    if not context.can_access_customer(customer_id):
        return _scope_violation()

    if not context.has_customer(customer_id):
        return _unknown_reference("customer")

    for record_id, has_fn, label in (
        (contact_id, context.has_contact, "contact"),
        (enquiry_id, context.has_enquiry, "enquiry"),
        (meeting_id, context.has_meeting, "meeting"),
        (call_id, context.has_call, "call"),
    ):
        if record_id is not None and not has_fn(customer_id, record_id):
            return _unknown_reference(label)

    return None