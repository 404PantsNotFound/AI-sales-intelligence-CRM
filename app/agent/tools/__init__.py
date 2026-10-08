from sqlalchemy.orm import Session, sessionmaker
from langchain_core.tools import StructuredTool

from app.database.connection import SessionLocal

from .activity_tools import build_activity_tools
from .analytics_tools import build_analytics_tools
from .customer_tools import build_customer_tools
from .context import AgentToolContext

__all__ = ["AgentToolContext", "build_read_only_tools"]


def build_read_only_tools(
    db: Session | None = None,
    context: AgentToolContext | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> list[StructuredTool]:
    if session_factory is not None:
        customer_kwargs = {
            "context": context,
            "session_factory": session_factory,
        }
        activity_kwargs = {
            "context": context,
            "session_factory": session_factory,
        }
        analytics_kwargs = {
            "session_factory": session_factory,
        }
    else:
        customer_kwargs = {
            "db": db,
            "context": context,
        }
        activity_kwargs = {
            "db": db,
            "context": context,
        }
        analytics_kwargs = {
            "db": db,
        }

    return [
        *build_customer_tools(**customer_kwargs),
        *build_activity_tools(**activity_kwargs),
        *build_analytics_tools(**analytics_kwargs),
    ]