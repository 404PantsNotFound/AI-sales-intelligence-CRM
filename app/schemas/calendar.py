from pydantic import BaseModel, ConfigDict, Field


class CalendarStatusResponse(BaseModel):
    configured: bool
    connected: bool
    status: str
    reminder_minutes: int
    failed_syncs: int


class CalendarConnectResponse(BaseModel):
    authorization_url: str


class CalendarPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_minutes: int = Field(ge=0, le=1440)
