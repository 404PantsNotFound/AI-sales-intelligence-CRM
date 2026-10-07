from langchain_core.language_models import BaseChatModel
from sqlalchemy.orm import Session

from app.agent.model import create_chat_model
from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services import customer_service
from app.services.agent_action_service import propose_or_answer


def chat_with_agent(
    db: Session,
    request: AgentChatRequest,
    *,
    model: BaseChatModel | None = None,
    user_id: int = 0,
) -> AgentChatResponse:
    if request.customer_id is not None:
        customer_service.get_customer(db, request.customer_id)
    return propose_or_answer(
        db,
        request,
        model=model or create_chat_model(),
        user_id=user_id,
    )

