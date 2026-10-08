from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain.agents import create_agent as _create_agent


def create_compatible_agent(
    model: str | BaseChatModel,
    tools: list[BaseTool] | None = None,
    **kwargs: Any,
):
    agent = _create_agent(
        model=model,
        tools=tools,
        **kwargs,
    )

    # get_graph() returns a visualization graph; routing is held by the
    # StateGraph builder retained on the compiled agent.
    builder = getattr(agent, "builder", None)
    branches = getattr(builder, "branches", None)
    model_branches = branches.get("model") if branches is not None else None
    branch = (
        model_branches.get("model_to_tools")
        if model_branches is not None
        else None
    )
    destinations = getattr(branch, "ends", None)
    if not isinstance(destinations, dict):
        raise RuntimeError(
            "The installed LangChain agent graph does not expose the expected "
            "model-to-tools routing branch."
        )

    # LangChain 1.4.3 can omit this valid loop destination when no middleware
    # or structured-output branch requires it during graph construction.
    destinations.setdefault("model", "model")

    return agent