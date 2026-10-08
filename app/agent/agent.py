from sqlalchemy.orm import Session

from app.agent.langchain_compat import create_compatible_agent
from langchain_core.language_models import BaseChatModel

from app.database.connection import SessionLocal

from app.agent.actions import action_checkpointer
from app.agent.model import create_chat_model
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import AgentToolContext, build_read_only_tools
from app.agent.tools.enrichment_tools import build_enrichment_tools
from app.agent.tools.write_tools import build_action_proposal_tools


def build_sales_agent(
    db: Session,
    *,
    model: BaseChatModel | None = None,
    allow_actions: bool = False,
    tool_context: AgentToolContext | None = None,
):
    tools = build_read_only_tools(
        context=tool_context,
        session_factory=SessionLocal,
    )

    if allow_actions:
        if tool_context is None:
            raise ValueError("Action-capable agent requires a CRM tool context.")

        tools.extend(
            build_enrichment_tools(
                db,
                tool_context,
            )
        )

        tools.extend(
            build_action_proposal_tools(
                tool_context,
            )
        )

    return create_compatible_agent(
        model=model or create_chat_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=action_checkpointer if allow_actions else None,
    )