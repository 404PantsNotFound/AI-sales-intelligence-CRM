from datetime import date

from sqlalchemy.orm import Session, sessionmaker
from langchain_core.tools import StructuredTool

from app.core.exceptions import APIError
from app.database.connection import SessionLocal
from app.schemas.activity import ActivityType
from app.schemas.call import CallResponse
from app.schemas.follow_up import FollowUpResponse
from app.schemas.meeting import MeetingResponse
from app.services import activity_service, call_service, followup_service, meeting_service

from .common import bounded_records, error_output, success_output
from .context import AgentToolContext
from .schemas import CustomerActivityInput, CustomerIdInput


def build_activity_tools(
    db: Session | None = None,
    context: AgentToolContext | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> list[StructuredTool]:

    factory = session_factory

    def get_session() -> tuple[Session, bool]:
        if factory is not None:
            return factory(), True
        if db is not None:
            return db, False
        return SessionLocal(), True

    def get_customer_meetings(customer_id: int) -> str:
        """List meetings recorded for the specified customer."""
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            meetings = meeting_service.list_customer_meetings(
                session,
                customer_id,
            )

            if context is not None:
                context.remember_customer(customer_id)

                for meeting in meetings:
                    context.remember_meeting(
                        customer_id,
                        meeting.meeting_id,
                    )

                    if meeting.contact_id is not None:
                        context.remember_contact(
                            customer_id,
                            meeting.contact_id,
                        )

                    if meeting.enquiry_id is not None:
                        context.remember_enquiry(
                            customer_id,
                            meeting.enquiry_id,
                        )

            records = [
                MeetingResponse.model_validate(meeting).model_dump(mode="json")
                for meeting in meetings
            ]

            return success_output(bounded_records(records))

        except APIError as exc:
            return error_output(exc)

        finally:
            if owns_session:
                session.close()

    def get_customer_calls(customer_id: int) -> str:
        """List calls recorded for the specified customer."""
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            calls = call_service.list_customer_calls(
                session,
                customer_id,
            )

            if context is not None:
                context.remember_customer(customer_id)

                for call in calls:
                    context.remember_call(
                        customer_id,
                        call.call_id,
                    )

                    if call.contact_id is not None:
                        context.remember_contact(
                            customer_id,
                            call.contact_id,
                        )

                    if call.enquiry_id is not None:
                        context.remember_enquiry(
                            customer_id,
                            call.enquiry_id,
                        )

            records = [
                CallResponse.model_validate(call).model_dump(mode="json")
                for call in calls
            ]

            return success_output(bounded_records(records))

        except APIError as exc:
            return error_output(exc)

        finally:
            if owns_session:
                session.close()

    def get_customer_followups(customer_id: int) -> str:
        """List the customer's follow-ups and their current status."""
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            followups = followup_service.list_customer_followups(
                session,
                customer_id,
            )

            if context is not None:
                context.remember_customer(customer_id)

                for followup in followups:
                    context.remember_followup(
                        customer_id,
                        followup.followup_id,
                    )

                    if followup.enquiry_id is not None:
                        context.remember_enquiry(
                            customer_id,
                            followup.enquiry_id,
                        )

                    if followup.meeting_id is not None:
                        context.remember_meeting(
                            customer_id,
                            followup.meeting_id,
                        )

                    if followup.call_id is not None:
                        context.remember_call(
                            customer_id,
                            followup.call_id,
                        )

            records = [
                FollowUpResponse.model_validate(followup).model_dump(mode="json")
                for followup in followups
            ]

            return success_output(bounded_records(records))

        except APIError as exc:
            return error_output(exc)

        finally:
            if owns_session:
                session.close()

    def get_customer_activity(
        customer_id: int,
        activity_type: ActivityType | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> str:
        """Retrieve recent enquiries, meetings, calls, and follow-ups for a customer.

        Optionally restrict results to one activity type and inclusive ISO date bounds.
        """
        session, owns_session = get_session()

        try:
            if context is not None:
                context.ensure_customer_access(customer_id)

            activity = activity_service.get_customer_activity(
                session,
                customer_id,
                activity_type=activity_type,
                start_date=start_date,
                end_date=end_date,
            )

            if context is not None:
                context.remember_customer(customer_id)

            records = [
                item.model_dump(mode="json")
                for item in activity
            ]

            return success_output(bounded_records(records))

        except APIError as exc:
            return error_output(exc)

        finally:
            if owns_session:
                session.close()

    return [
        StructuredTool.from_function(
            func=get_customer_meetings,
            name="get_customer_meetings",
            description=get_customer_meetings.__doc__,
            args_schema=CustomerIdInput,
        ),
        StructuredTool.from_function(
            func=get_customer_calls,
            name="get_customer_calls",
            description=get_customer_calls.__doc__,
            args_schema=CustomerIdInput,
        ),
        StructuredTool.from_function(
            func=get_customer_followups,
            name="get_customer_followups",
            description=get_customer_followups.__doc__,
            args_schema=CustomerIdInput,
        ),
        StructuredTool.from_function(
            func=get_customer_activity,
            name="get_customer_activity",
            description=get_customer_activity.__doc__,
            args_schema=CustomerActivityInput,
        ),
    ]