import logging

from langchain_core.language_models import BaseChatModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.model import create_chat_model
from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services import agent_conversation_service, customer_service
from app.services.agent_action_service import propose_or_answer

logger = logging.getLogger(__name__)


def chat_with_agent(
    db: Session,
    request: AgentChatRequest,
    *,
    model: BaseChatModel | None = None,
    user_id: int = 0,
    organization_id: int,
) -> AgentChatResponse:
    if request.customer_id is not None:
        customer_service.get_customer(db, request.customer_id, organization_id=organization_id)
    # Validated before the model runs: only this user's conversation for this customer.
    conversation = agent_conversation_service.resolve(
        db,
        request.conversation_id,
        user_id=user_id,
        customer_id=request.customer_id,
    )
    history = agent_conversation_service.model_history(db, conversation)
    # IDs, counts and roles only; message text is never logged.
    logger.info(
        "Agent chat context [user_id=%s customer_id=%s conversation_id=%s "
        "history_messages=%s history_roles=%s]",
        user_id,
        request.customer_id,
        conversation.conversation_id if conversation else "new",
        len(history),
        ",".join(item["role"][0] for item in history) or "-",
    )
    response = propose_or_answer(
        db,
        request,
        model=model or create_chat_model(),
        user_id=user_id,
        organization_id=organization_id,
        history=history,
    )
    # A successful CRM lookup grounds the answer in records, not chat memory, so it is kept.
    if (
        response.pending_action is None
        and response.clarification_task is None
        and not any(call.success for call in response.tool_calls)
    ):
        replacement = agent_conversation_service.unverified_recall_reply(
            request.message, response.response, history
        )
        if replacement is not None:
            logger.warning(
                "Unverified recall answer replaced [customer_id=%s conversation_id=%s]",
                request.customer_id,
                conversation.conversation_id if conversation else "new",
            )
            response.response = replacement
    try:
        response.conversation_id = agent_conversation_service.append_turn(
            db,
            conversation,
            user_id=user_id,
            customer_id=request.customer_id,
            user_message=request.message,
            assistant_message=response.response,
        )
    except SQLAlchemyError as exc:
        # The reply and any proposal are already committed; losing one history turn is safer than a 500.
        db.rollback()
        logger.error(
            "Agent conversation turn could not be saved [error_type=%s]",
            type(exc).__name__,
        )
        response.conversation_id = conversation.conversation_id if conversation else None
    return response
