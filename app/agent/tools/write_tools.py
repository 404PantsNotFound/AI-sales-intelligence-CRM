from datetime import date, datetime, time
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
    CreateMeetingTaskInput,
    CreateMeetingProposal,
    RecordCallResultProposal,
    ScheduleCallProposal,
)
from .context import AgentToolContext
from app.schemas.validators import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
)
from app.services.scheduling_service import (
    ScheduleRequest,
    default_duration,
    inspect_availability,
)
from sqlalchemy.orm import Session


def build_action_proposal_tools(
    context: AgentToolContext,
    db: Session,
) -> list[StructuredTool]:
    def _prepare(
        action: AgentActionName,
        payload: dict[str, Any],
    ) -> str:
        interrupt({"action": action, "payload": payload})
        return "The action still requires confirmation through the confirmation endpoint."

    def _availability_message(
        result: dict[str, object],
    ) -> str | None:
        if result["available"]:
            return None
        conflicts = result["conflicts"]
        suggestions = result["suggestions"]
        conflict_types = sorted(
            {
                str(item["activity_type"])
                for item in conflicts
                if isinstance(item, dict)
            }
        )
        labels = ", ".join(conflict_types) or "another scheduled activity"
        options = [
            str(item["starts_at"])
            for item in suggestions
            if isinstance(item, dict) and isinstance(item.get("starts_at"), str)
        ]
        if options:
            return (
                f"That time conflicts with {labels}. Available alternatives "
                f"in {result['timezone']} are: {'; '.join(options)}. "
                "Ask which alternative the user wants. No proposal was created."
            )
        return (
            f"That time conflicts with {labels}, and no available alternative "
            "was found in the configured suggestion window. No proposal was created."
        )

    def create_meeting(
        customer_id: int,
        scheduled_date: date | None = None,
        scheduled_time: time | None = None,
        scheduled_timezone: str | None = None,
        scheduled_time_occurrence: Literal["earlier", "later"] | None = None,
        contact_id: int | None = None,
        enquiry_id: int | None = None,
        duration: int | None = None,
        status: MeetingStatus = "scheduled",
        agenda: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Collect a specific meeting date, time, and timezone before proposing. Never invent a schedule."""
        task_data = CreateMeetingTaskInput.model_validate(
            {
                "customer_id": customer_id,
                "scheduled_date": scheduled_date,
                "scheduled_time": scheduled_time,
                "scheduled_timezone": scheduled_timezone or context.timezone_name,
                "scheduled_time_occurrence": scheduled_time_occurrence,
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
            customer_id=task_data.customer_id,
            contact_id=task_data.contact_id,
            enquiry_id=task_data.enquiry_id,
        )

        if invalid:
            return invalid

        values = task_data.model_dump(mode="json", exclude_none=True)
        if (
            task_data.scheduled_date is None
            or task_data.scheduled_time is None
            or task_data.scheduled_timezone is None
        ):
            interrupt(
                {
                    "clarification": True,
                    "action": "create_meeting",
                    "payload": values,
                }
            )
            return "Ask for each missing meeting schedule detail before proposing it."

        try:
            scheduled_at = task_data.resolve_scheduled_at()
        except (AmbiguousLocalTimeError, NonexistentLocalTimeError):
            interrupt(
                {
                    "clarification": True,
                    "action": "create_meeting",
                    "payload": task_data.model_dump(mode="json", exclude_none=True),
                }
            )
            return "Clarify the daylight-saving time before proposing the meeting."
        proposal_values = task_data.model_dump(
            exclude={
                "scheduled_date",
                "scheduled_time",
                "scheduled_time_occurrence",
            }
        )
        proposal_values["scheduled_at"] = scheduled_at
        proposal = CreateMeetingProposal.model_validate(proposal_values)
        if proposal.status == "scheduled":
            availability = inspect_availability(
                db,
                ScheduleRequest(
                    activity_type="meeting",
                    starts_at=proposal.scheduled_at,
                    duration_minutes=proposal.duration
                    or default_duration("meeting"),
                    timezone_name=proposal.scheduled_timezone or "UTC",
                ),
            )
            conflict_message = _availability_message(availability)
            if conflict_message:
                return conflict_message

        return _prepare(
            "create_meeting",
            proposal.model_dump(mode="json"),
        )

    def create_followup(
        customer_id: int,
        type: str,
        due_date: datetime,
        due_timezone: str | None = None,
        due_time_occurrence: Literal["earlier", "later"] | None = None,
        duration: int | None = None,
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
                "due_timezone": due_timezone,
                "due_time_occurrence": due_time_occurrence,
                "duration": duration,
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

        if data.status in {"pending", "in_progress", "overdue"}:
            availability = inspect_availability(
                db,
                ScheduleRequest(
                    activity_type="followup",
                    starts_at=data.due_date,
                    duration_minutes=data.duration
                    or default_duration("followup"),
                    timezone_name=data.due_timezone or "UTC",
                ),
            )
            conflict_message = _availability_message(availability)
            if conflict_message:
                return conflict_message

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

    def schedule_call(
        customer_id: int,
        scheduled_at: datetime,
        scheduled_timezone: str,
        scheduled_time_occurrence: Literal["earlier", "later"] | None = None,
        duration: int | None = None,
        contact_id: int | None = None,
        enquiry_id: int | None = None,
        call_type: str | None = None,
        status: Literal["scheduled"] = "scheduled",
        notes: str | None = None,
    ) -> str:
        """Propose a specific scheduled call after checking availability; confirmation is still required."""
        data = ScheduleCallProposal.model_validate(
            {
                "customer_id": customer_id,
                "scheduled_at": scheduled_at,
                "scheduled_timezone": scheduled_timezone,
                "scheduled_time_occurrence": scheduled_time_occurrence,
                "duration": duration,
                "contact_id": contact_id,
                "enquiry_id": enquiry_id,
                "call_type": call_type,
                "notes": notes,
                "status": status,
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
        availability = inspect_availability(
            db,
            ScheduleRequest(
                activity_type="call",
                starts_at=data.scheduled_at,
                duration_minutes=data.duration or default_duration("call"),
                timezone_name=data.scheduled_timezone or "UTC",
            ),
        )
        conflict_message = _availability_message(availability)
        if conflict_message:
            return conflict_message
        return _prepare("schedule_call", data.model_dump(mode="json"))

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
            args_schema=CreateMeetingTaskInput,
        ),
        StructuredTool.from_function(
            func=create_followup,
            name="create_followup",
            description=create_followup.__doc__,
            args_schema=CreateFollowupProposal,
        ),
        StructuredTool.from_function(
            func=schedule_call,
            name="schedule_call",
            description=schedule_call.__doc__,
            args_schema=ScheduleCallProposal,
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