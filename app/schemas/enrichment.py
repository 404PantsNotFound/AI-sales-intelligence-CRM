from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

class CRMEnrichmentInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    customer_id: int = Field(ge=1)
    enquiry_id: int | None = Field(default=None, ge=1)

    source_type: str = Field(
        default="call",
        min_length=1,
        max_length=50,
    )
    source_text: str = Field(
        min_length=1,
        max_length=10000,
    )


class CRMEnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sales_stage: Literal[
        "new",
        "qualified",
        "proposal",
        "negotiation",
        "won",
        "lost",
    ] | None = None

    customer_status: Literal[
        "active",
        "inactive",
        "prospect",
    ] | None = None

    enquiry_priority: Literal[
        "low",
        "medium",
        "normal",
        "high",
        "urgent",
    ] | None = None

    enquiry_status: Literal[
        "open",
        "in_progress",
        "converted",
        "closed",
        "lost",
    ] | None = None

    estimated_value: Decimal | None = Field(
        default=None,
        ge=0,
    )

    reasoning: list[str] = Field(
        default_factory=list,
        max_length=10,
    )