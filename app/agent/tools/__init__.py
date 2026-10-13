import json
from typing import Any

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
            "context": context,
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
            "context": context,
        }

    tools = [
        *build_customer_tools(**customer_kwargs),
        *build_activity_tools(**activity_kwargs),
        *build_analytics_tools(**analytics_kwargs),
    ]
    assert context is not None  # The builders above refuse to run without one.
    return [_answer_once_per_request(tool, context) for tool in tools]


def _answer_once_per_request(tool: StructuredTool, context: AgentToolContext) -> StructuredTool:
    """Repeat calls with identical arguments get a pointer to the earlier result, not a re-read."""
    func = tool.func
    assert func is not None

    def run(**kwargs: Any) -> str:
        key = f"{tool.name}:{json.dumps(kwargs, sort_keys=True, default=str)}"
        if key in context.retrieved_calls:
            return json.dumps(
                {"note": "Already retrieved in this request with the same arguments; reuse that result."}
            )
        result = func(**kwargs)
        if '"error"' not in result[:10]:  # Failed reads may be retried.
            context.retrieved_calls.add(key)
        return result

    return StructuredTool.from_function(
        func=run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )