"""Orquestación de una petición de chat con respuesta en streaming (SSE)."""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Message, MessageRole
from app.services.conversations import ConversationRepository, make_title
from app.services.llm import ChatMessage, LLMClient, LLMError

logger = logging.getLogger(__name__)


class ConversationNotFoundError(Exception):
    """La conversación no existe o no pertenece al usuario."""


def sse_event(event: str, data: dict[str, Any]) -> str:
    """Serializa un evento Server-Sent Events."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_prompt(system_prompt: str, history: Sequence[Message]) -> list[ChatMessage]:
    """Construye la lista de mensajes que se envía al modelo."""
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages += [
        ChatMessage(role="user" if m.role == MessageRole.USER else "assistant", content=m.content)
        for m in history
    ]
    return messages


@dataclass(slots=True)
class PreparedChat:
    """Estado de la petición tras guardar el mensaje del usuario."""

    user_id: str
    conversation_id: str
    title: str
    user_message_id: int
    prompt: list[ChatMessage]


class ChatService:
    """Gestiona el ciclo de una pregunta: persistencia, llamada al modelo y streaming."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._llm = llm
        self._settings = settings

    async def prepare(self, user_id: str, conversation_id: str | None, text: str) -> PreparedChat:
        """Guarda el mensaje del usuario y prepara el prompt.

        Se ejecuta antes de empezar el streaming para poder responder 404 si procede.
        """
        async with self._sessionmaker() as session:
            repo = ConversationRepository(session)
            if conversation_id is None:
                conversation = await repo.create(user_id, title=make_title(text))
            else:
                found = await repo.get_for_user(conversation_id, user_id)
                if found is None:
                    raise ConversationNotFoundError(conversation_id)
                conversation = found
            user_message = await repo.add_message(conversation, MessageRole.USER, text)
            history = await repo.recent_messages(
                conversation.id, self._settings.chat_history_max_messages + 1
            )
            await session.commit()
            return PreparedChat(
                user_id=user_id,
                conversation_id=conversation.id,
                title=conversation.title,
                user_message_id=user_message.id,
                prompt=build_prompt(self._settings.default_system_prompt, history),
            )

    async def stream(self, prepared: PreparedChat) -> AsyncIterator[str]:
        """Emite los eventos SSE de la respuesta y guarda el mensaje del asistente."""
        yield sse_event(
            "conversation",
            {
                "id": prepared.conversation_id,
                "title": prepared.title,
                "user_message_id": prepared.user_message_id,
            },
        )
        parts: list[str] = []
        try:
            async for delta in self._llm.stream_chat(prepared.prompt):
                parts.append(delta)
                yield sse_event("token", {"content": delta})
        except LLMError:
            logger.exception("Fallo del modelo en la conversación %s", prepared.conversation_id)
            yield sse_event(
                "error", {"detail": "El modelo no está disponible. Inténtalo de nuevo."}
            )
            return

        answer = "".join(parts).strip()
        async with self._sessionmaker() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_for_user(prepared.conversation_id, prepared.user_id)
            if conversation is None:  # borrada mientras se generaba la respuesta
                yield sse_event("error", {"detail": "La conversación ya no existe."})
                return
            message = await repo.add_message(conversation, MessageRole.ASSISTANT, answer)
            await session.commit()
        yield sse_event("done", {"message_id": message.id})
