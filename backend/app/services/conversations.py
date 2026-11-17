"""Acceso a datos de conversaciones y mensajes."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, MessageRole, utcnow

TITLE_MAX_LENGTH = 60
DEFAULT_TITLE = "Nueva conversación"


def make_title(text: str) -> str:
    """Genera un título corto a partir del primer mensaje."""
    title = re.sub(r"\s+", " ", text).strip()
    if not title:
        return DEFAULT_TITLE
    if len(title) <= TITLE_MAX_LENGTH:
        return title
    return title[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


class ConversationRepository:
    """Operaciones sobre conversaciones, siempre acotadas a un usuario."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: str) -> list[Conversation]:
        result = await self._session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result)

    async def get_for_user(self, conversation_id: str, user_id: str) -> Conversation | None:
        conversation = await self._session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        return conversation

    async def create(self, user_id: str, title: str = DEFAULT_TITLE) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def rename(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        conversation.updated_at = utcnow()
        await self._session.flush()
        return conversation

    async def delete(self, conversation: Conversation) -> None:
        await self._session.delete(conversation)
        await self._session.flush()

    async def add_message(
        self, conversation: Conversation, role: MessageRole, content: str
    ) -> Message:
        message = Message(conversation_id=conversation.id, role=role.value, content=content)
        self._session.add(message)
        conversation.updated_at = utcnow()
        await self._session.flush()
        return message

    async def list_messages(self, conversation_id: str) -> list[Message]:
        result = await self._session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )
        return list(result)

    async def recent_messages(self, conversation_id: str, limit: int) -> list[Message]:
        """Últimos `limit` mensajes en orden cronológico."""
        if limit <= 0:
            return []
        result = await self._session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(list(result)))
