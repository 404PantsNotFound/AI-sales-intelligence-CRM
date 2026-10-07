from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    customer_id: int | None = Field(default=None, ge=1)


class AgentToolCall(BaseModel):
    name: str
    input_summary: dict[str, str | int]
    success: bool
    error_code: str | None = None


AgentActionName = Literal[
    "create_meeting",
    "create_followup",
    "record_call_result",
    "complete_followup",
]


class PendingAgentAction(BaseModel):
    action_id: str
    action: AgentActionName
    payload: dict[str, Any]


class AgentActionResult(BaseModel):
    status: Literal["completed", "cancelled", "failed"]
    action: AgentActionName
    record_id: int | None = None
    record: dict[str, Any] | None = None
    error_code: str | None = None
    message: str


class AgentChatResponse(BaseModel):
    response: str
    customer_id: int | None
    tool_calls: list[AgentToolCall]
    pending_action: PendingAgentAction | None = None


class CustomerSummaryOutput(BaseModel):
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    customer_concerns: list[str] = Field(default_factory=list)
    recommended_next_action: str = Field(min_length=1)


class CustomerSummaryResponse(BaseModel):
    customer_id: int
    summary: str
    key_points: list[str]
    customer_concerns: list[str]
    open_followups: list[str]
    recommended_next_action: str
    generated_at: str


class MeetingBriefOutput(BaseModel):
    brief: str = Field(min_length=1)
    customer_overview: str = Field(min_length=1)
    current_requirement: str = Field(min_length=1)
    unresolved_issues: list[str] = Field(default_factory=list)
    recommended_talking_points: list[str] = Field(default_factory=list)
    recommended_next_action: str = Field(min_length=1)


class MeetingBriefResponse(BaseModel):
    meeting_id: int
    customer_id: int
    brief: str
    customer_overview: str
    current_requirement: str
    previous_discussions: list[str]
    unresolved_issues: list[str]
    recommended_talking_points: list[str]
    recommended_next_action: str
    generated_at: str
