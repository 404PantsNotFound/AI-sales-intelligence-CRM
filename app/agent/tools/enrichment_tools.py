import json

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from app.agent.model import (
    create_chat_model,
    map_ai_provider_exception,
)
from app.core.exceptions import APIError
from app.schemas.enrichment import (
    CRMEnrichmentInput,
    CRMEnrichmentResult,
)

from .common import error_output, success_output
from .context import AgentToolContext


ENRICHMENT_PROMPT = """
You are a CRM data-enrichment assistant.

Analyze the supplied CRM activity and identify only concrete CRM field changes
that are directly supported by the supplied text.

Never invent CRM information.
Never infer a value that is not supported by the activity.
If a field cannot be confidently determined, return null.

Allowed customer fields:

sales_stage:
- new
- qualified
- proposal
- negotiation
- won
- lost

customer_status:
- active
- inactive
- prospect

Allowed enquiry fields:

enquiry_priority:
- low
- normal
- high
- urgent

enquiry_status:
- open
- in_progress
- converted
- closed
- lost

estimated_value:
- non-negative numeric value

Only return a field when the activity provides concrete evidence for that
change. Do not change fields merely because another value seems more likely.

The reasoning field should briefly explain the evidence supporting each
proposed change.

Return only the structured enrichment result.
"""


def build_enrichment_tools(
    db: Session,
    context: AgentToolContext | None = None,
) -> list[StructuredTool]:

    def analyze_crm_activity(
        customer_id: int,
        source_text: str,
        source_type: str = "call",
        enquiry_id: int | None = None,
    ) -> str:
        """Analyze CRM activity and propose structured field enrichment without modifying the database."""

        try:
            payload = CRMEnrichmentInput.model_validate(
                {
                    "customer_id": customer_id,
                    "enquiry_id": enquiry_id,
                    "source_type": source_type,
                    "source_text": source_text,
                }
            )

            if context is not None:
                context.ensure_customer_access(payload.customer_id)

                if not context.has_customer(payload.customer_id):
                    return error_output(
                        APIError(
                            "Retrieve the customer through a CRM read tool before enrichment.",
                            status_code=400,
                            code="record_not_retrieved",
                        )
                    )

                if (
                    payload.enquiry_id is not None
                    and not context.has_enquiry(
                        payload.customer_id,
                        payload.enquiry_id,
                    )
                ):
                    return error_output(
                        APIError(
                            "Retrieve the enquiry through a CRM read tool before enrichment.",
                            status_code=400,
                            code="record_not_retrieved",
                        )
                    )

            model = create_chat_model()

            structured_model = model.with_structured_output(
                CRMEnrichmentResult
            )

            result = structured_model.invoke(
                [
                    ("system", ENRICHMENT_PROMPT),
                    (
                        "human",
                        json.dumps(
                            payload.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                    ),
                ]
            )

            enrichment = CRMEnrichmentResult.model_validate(result)

            return success_output(
                {
                    "customer_id": payload.customer_id,
                    "enquiry_id": payload.enquiry_id,
                    "source_type": payload.source_type,
                    "enrichment": enrichment.model_dump(
                        mode="json"
                    ),
                    "requires_confirmation": True,
                }
            )

        except APIError as exc:
            return error_output(exc)

        except Exception as exc:
            return error_output(
                map_ai_provider_exception(
                    exc,
                    operation="crm_activity_enrichment",
                )
            )

    return [
        StructuredTool.from_function(
            func=analyze_crm_activity,
            name="analyze_crm_activity",
            description=analyze_crm_activity.__doc__,
            args_schema=CRMEnrichmentInput,
        )
    ]