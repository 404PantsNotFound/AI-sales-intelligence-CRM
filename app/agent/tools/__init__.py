from sqlalchemy.orm import Session
from langchain_core.tools import StructuredTool

from .activity_tools import build_activity_tools
from .analytics_tools import build_analytics_tools
from .customer_tools import build_customer_tools
from .context import AgentToolContext

__all__ = ["AgentToolContext", "build_read_only_tools"]


def build_read_only_tools(
    db: Session,
    context: AgentToolContext | None = None,
) -> list[StructuredTool]:
    return [
        *build_customer_tools(db, context),
        *build_activity_tools(db, context),
        *build_analytics_tools(db),
    ]
