"""Durable CRM assistant chat history, isolated by user and customer.

Only the user's messages and the assistant's final replies are stored. Tool
output, CRM records and HITL task/approval state stay in their own tables, so
history never becomes a second source of CRM facts or approval state.
"""

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import APIError
from app.models import AgentConversation, AgentConversationMessage
from app.schemas.agent import AgentConversationMessageResponse, AgentConversationResponse

# Per-message cap on what is replayed to the model, to bound tokens.
HISTORY_MESSAGE_CHARS = 2000
_STORED_MESSAGE_CHARS = 16000


def resolve(
    db: Session,
    conversation_id: UUID | None,
    *,
    user_id: int,
    customer_id: int | None,
) -> AgentConversation | None:
    """Return the caller's conversation, or None to start a new one.

    An unknown ID, another user's conversation and another customer's
    conversation all get the same 404, so IDs cannot be probed.
    """
    if conversation_id is None:
        return None
    conversation = db.get(AgentConversation, str(conversation_id))
    if (
        conversation is None
        or conversation.owner_user_id != user_id
        or conversation.customer_id != customer_id
    ):
        raise APIError("Conversation not found.", 404, "conversation_not_found")
    return conversation


def _messages(db: Session, conversation_id: str, limit: int) -> list[AgentConversationMessage]:
    if limit <= 0:
        return []
    rows = db.scalars(
        select(AgentConversationMessage)
        .where(AgentConversationMessage.conversation_id == conversation_id)
        .order_by(AgentConversationMessage.message_id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def model_history(db: Session, conversation: AgentConversation | None) -> list[dict[str, str]]:
    """The most recent turns, oldest first, as chat messages for the model."""
    if conversation is None:
        return []
    return [
        {"role": row.role, "content": row.content[:HISTORY_MESSAGE_CHARS]}
        for row in _messages(db, conversation.conversation_id, settings.agent_history_max_messages)
    ]


def append_turn(
    db: Session,
    conversation: AgentConversation | None,
    *,
    user_id: int,
    customer_id: int | None,
    user_message: str,
    assistant_message: str,
) -> str:
    if conversation is None:
        conversation = AgentConversation(
            conversation_id=str(uuid4()),
            owner_user_id=user_id,
            customer_id=customer_id,
        )
        db.add(conversation)
        db.flush()  # The messages' FK needs the parent row first.
    for role, content in (("user", user_message), ("assistant", assistant_message)):
        db.add(
            AgentConversationMessage(
                conversation_id=conversation.conversation_id,
                role=role,
                content=content[:_STORED_MESSAGE_CHARS],
            )
        )
    db.commit()
    return conversation.conversation_id


def latest(
    db: Session,
    *,
    user_id: int,
    customer_id: int | None,
    limit: int = 50,
) -> AgentConversationResponse | None:
    # Newest message ID, not a timestamp: MySQL DATETIME has one-second resolution.
    conversation = db.scalar(
        select(AgentConversation)
        .join(
            AgentConversationMessage,
            AgentConversationMessage.conversation_id == AgentConversation.conversation_id,
        )
        .where(
            AgentConversation.owner_user_id == user_id,
            AgentConversation.customer_id.is_(None)
            if customer_id is None
            else AgentConversation.customer_id == customer_id,
        )
        .order_by(AgentConversationMessage.message_id.desc())
        .limit(1)
    )
    if conversation is None:
        return None
    return AgentConversationResponse(
        conversation_id=conversation.conversation_id,
        customer_id=conversation.customer_id,
        messages=[
            AgentConversationMessageResponse.model_validate(row, from_attributes=True)
            for row in _messages(db, conversation.conversation_id, limit)
        ],
    )


# "What test code did I give you?", "What was my code?", "Do you remember ...?"
_RECALL_QUESTION = re.compile(
    r"\b(?:did|have)\s+I\s+(?:give|gave|tell|told|share|shared|mention|mentioned|say|said|send|sent)\b"
    r"|\bI\s+(?:gave|told|shared|mentioned|said|sent)\s+you\b"
    r"|\bwhat\s+(?:was|is|were)\s+my\b"
    r"|\bdo\s+you\s+remember\b",
    re.IGNORECASE,
)
# Whole codes, numbers and other values containing a digit, e.g. "ALPHA-9981" or "3".
_RECALLED_VALUE = re.compile(r"[\w-]*\d[\w-]*")


def _values(text: str) -> set[str]:
    return {value.strip("-").casefold() for value in _RECALLED_VALUE.findall(text)}


def unverified_recall_reply(
    question: str,
    answer: str,
    history: list[dict[str, str]],
) -> str | None:
    """Return a fixed reply when a memory-recall answer cites a value the user never gave.

    Only the user's own earlier messages in this conversation (as sent to the
    model) count as proof: assistant replies are model output and may themselves
    be invented, and the question itself proves nothing ("Was it X?" -> "Yes, X").
    Each claimed value must match a whole value the user wrote, so a truncated or
    altered code ("NEBULA-731") cannot pass as a substring of "NEBULA-7316".
    """
    if not _RECALL_QUESTION.search(question):
        return None
    verified: set[str] = set()
    for item in history:
        if item["role"] == "user":
            verified |= _values(item["content"])
    if _values(answer) <= verified:
        return None
    if "test code" in question.casefold():
        return "I can't find a test code in this conversation."
    return "I can't find that information in this conversation."
