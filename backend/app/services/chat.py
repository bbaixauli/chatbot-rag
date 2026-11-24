"""Orquestación de una petición de chat con respuesta en streaming (SSE)."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig
from app.db.models import (
    AccessKind,
    Document,
    Message,
    MessageRole,
    MessageSource,
    QuestionStatus,
    ScopeSource,
    utcnow,
)
from app.services.condense import condense_question
from app.services.conversations import ConversationRepository, make_title
from app.services.dashboard import QuestionRecord, record_question
from app.services.document_names import detect_documents
from app.services.llm import ChatMessage, LLMClient, LLMError
from app.services.prompts import PromptRepository
from app.services.rag import ContextSource, Retriever, build_system_prompt, extract_citations
from app.services.scope import ScopeDocument, indexed_documents, resolve_scope

logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class ConversationNotFoundError(Exception):
    """La conversación no existe o no pertenece al usuario."""


def sse_event(event: str, data: dict[str, Any]) -> str:
    """Serializa un evento Server-Sent Events."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_chat_messages(history: Sequence[Message]) -> list[ChatMessage]:
    """Convierte el historial guardado al formato del modelo."""
    return [
        ChatMessage(role="user" if m.role == MessageRole.USER else "assistant", content=m.content)
        for m in history
    ]


def select_sources(
    sources: Sequence[ContextSource], answer: str
) -> list[tuple[ContextSource, bool]]:
    """Fuentes que se muestran con la respuesta y si fueron citadas.

    Si el modelo cita, solo se muestran las citadas. Si no cita nada (habitual en
    modelos pequeños), se muestran todas como «consultadas».
    """
    cited = set(extract_citations(answer, len(sources)))
    if cited:
        return [(s, True) for s in sources if s.number in cited]
    return [(s, False) for s in sources]


def source_payload(source: ContextSource, cited: bool) -> dict[str, Any]:
    return {
        "number": source.number,
        "document_id": source.document_id,
        "filename": source.filename,
        "page": source.page,
        "heading": source.heading,
        "snippet": source.snippet,
        "cited": cited,
    }


@dataclass(slots=True)
class Requester:
    """Quién pregunta y desde dónde (para la auditoría, #50)."""

    ip: str | None = None
    user_agent: str | None = None
    display_name: str | None = None


@dataclass(slots=True)
class PreparedChat:
    """Estado de la petición tras guardar el mensaje del usuario."""

    user_id: str
    conversation_id: str
    title: str
    user_message_id: int
    question: str
    system_prompt: str
    history: list[ChatMessage]
    # Para el cuadro de mando (#51).
    asked_at: datetime = field(default_factory=utcnow)
    started: float = field(default_factory=time.perf_counter)
    session_id: str | None = None
    access_kind: AccessKind = AccessKind.SHARED
    # Auditoría (#50).
    requester: Requester = field(default_factory=Requester)
    prompt_version_id: int | None = None
    # Documentos a los que se limita la búsqueda (#58); vacía: todos.
    scope: list[ScopeDocument] = field(default_factory=list)


class ChatService:
    """Gestiona el ciclo de una pregunta: persistencia, llamada al modelo y streaming."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        settings: Settings,
        config: RuntimeConfig,
        retriever: Retriever | None = None,
    ) -> None:
        """`config` es la configuración vigente al recibir la pregunta: se usa la misma de
        principio a fin aunque se cambie en la administración mientras tanto."""
        self._sessionmaker = sessionmaker
        self._llm = llm
        self._settings = settings
        self._config = config
        self._retriever = retriever

    async def prepare(
        self,
        user_id: str,
        conversation_id: str | None,
        text: str,
        *,
        session_id: str | None = None,
        access_kind: AccessKind = AccessKind.SHARED,
        requester: Requester | None = None,
        document_ids: Sequence[str] | None = None,
    ) -> PreparedChat:
        """Guarda el mensaje del usuario y prepara el prompt.

        Se ejecuta antes de empezar el streaming para poder responder 404 (conversación) o
        422 (`ScopeError`: algún documento de `document_ids` no existe) si procede.
        `document_ids` cambia la limitación de la búsqueda de la conversación (ver
        `resolve_scope`).
        """
        started = time.perf_counter()
        async with self._sessionmaker() as session:
            repo = ConversationRepository(session)
            if conversation_id is None:
                conversation = await repo.create(user_id, title=make_title(text))
            else:
                found = await repo.get_for_user(conversation_id, user_id)
                if found is None:
                    raise ConversationNotFoundError(conversation_id)
                conversation = found
            scope = await resolve_scope(session, conversation, document_ids)
            user_message = await repo.add_message(conversation, MessageRole.USER, text)
            history = await repo.recent_messages(
                conversation.id, self._config.chat.history_max_messages + 1
            )
            system_prompt = await PromptRepository(
                session, self._settings.default_system_prompt
            ).current()
            await session.commit()
            return PreparedChat(
                user_id=user_id,
                conversation_id=conversation.id,
                title=conversation.title,
                user_message_id=user_message.id,
                question=text,
                system_prompt=system_prompt.content,
                history=to_chat_messages(history),
                asked_at=user_message.created_at,
                started=started,
                session_id=session_id,
                access_kind=access_kind,
                requester=requester or Requester(),
                prompt_version_id=system_prompt.version.id if system_prompt.version else None,
                scope=scope,
            )

    async def stream(self, prepared: PreparedChat) -> AsyncIterator[str]:
        """Emite los eventos SSE de la respuesta y guarda el mensaje del asistente.

        Al terminar, por cualquier motivo (respuesta, error del modelo o la persona cierra la
        conexión), la pregunta queda en el registro del cuadro de mando.
        """
        record = QuestionRecord(
            asked_at=prepared.asked_at,
            session_id=prepared.session_id,
            user_id=prepared.user_id,
            access_kind=prepared.access_kind.value,
            conversation_id=prepared.conversation_id,
            question=prepared.question,
            client_ip=prepared.requester.ip,
            user_agent=prepared.requester.user_agent,
            user_display_name=prepared.requester.display_name,
            model=self._config.llm.model,
            prompt_version_id=prepared.prompt_version_id,
        )
        try:
            async for event in self._stream(prepared, record):
                yield event
        finally:
            record.response_ms = _elapsed_ms(prepared.started)
            # Protegido: si la petición se cancela, el registro se completa igualmente.
            await asyncio.shield(record_question(self._sessionmaker, record))

    async def _stream(self, prepared: PreparedChat, record: QuestionRecord) -> AsyncIterator[str]:
        yield sse_event(
            "conversation",
            {
                "id": prepared.conversation_id,
                "title": prepared.title,
                "user_message_id": prepared.user_message_id,
            },
        )
        sources: list[ContextSource] = []
        scope_payload: dict[str, Any] | None = None
        if self._retriever:
            query = await self._search_query(prepared)
            record.search_query = query
            # La limitación elegida en el chat tiene prioridad; si no hay, se busca en los
            # documentos que nombre la pregunta (o su reformulación, en las de seguimiento).
            scope, origin = prepared.scope, ScopeSource.SELECTOR
            if not scope:
                scope = await self._detect_scope(prepared.question, query)
                origin = ScopeSource.DETECTED
            scope_ids = [document.id for document in scope]
            if scope_ids:
                record.scope_document_ids = scope_ids
                record.scope_source = origin.value
                scope_payload = {
                    "documents": [{"id": d.id, "filename": d.filename} for d in scope],
                    "source": origin.value,
                }
                yield sse_event("scope", scope_payload)
            sources = await self._retriever.retrieve(query, scope_ids or None)
        prompt = [
            ChatMessage(
                role="system", content=build_system_prompt(prepared.system_prompt, sources)
            ),
            *prepared.history,
        ]
        record.sources_retrieved = len(sources)
        parts: list[str] = []
        try:
            async for delta in self._llm.stream_chat(prompt):
                if not parts:
                    record.first_token_ms = _elapsed_ms(prepared.started)
                parts.append(delta)
                yield sse_event("token", {"content": delta})
        except LLMError as exc:
            record.status = QuestionStatus.ERROR
            record.answer = "".join(parts).strip() or None
            record.error_detail = str(exc)
            logger.error(
                "Fallo del modelo en la conversación %s: %s",
                prepared.conversation_id,
                exc,
                extra={"conversation_id": prepared.conversation_id, "partial": bool(parts)},
            )
            yield sse_event("error", {"detail": exc.user_message})
            return

        answer = "".join(parts).strip()
        if not answer:
            record.status = QuestionStatus.ERROR
            record.error_detail = "El modelo devolvió una respuesta vacía"
            # Respuesta vacía (p. ej. el modelo agotó los tokens o filtró la salida).
            logger.warning(
                "El modelo devolvió una respuesta vacía en la conversación %s",
                prepared.conversation_id,
            )
            yield sse_event(
                "error",
                {"detail": "El modelo no ha devuelto ninguna respuesta. Inténtalo de nuevo."},
            )
            return
        record.status = QuestionStatus.OK
        record.answer = answer
        shown = select_sources(sources, answer)
        record.sources_cited = sum(1 for _, cited in shown if cited)
        record.cited_documents = [
            {
                "document_id": source.document_id,
                "filename": source.filename,
                "page": source.page,
                "cited": cited,
            }
            for source, cited in shown
        ]
        if shown:
            yield sse_event("sources", {"sources": [source_payload(s, c) for s, c in shown]})
        async with self._sessionmaker() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_for_user(prepared.conversation_id, prepared.user_id)
            if conversation is None:  # borrada mientras se generaba la respuesta
                record.error_detail = "La conversación se borró mientras se respondía"
                yield sse_event("error", {"detail": "La conversación ya no existe."})
                return
            message = await repo.add_message(conversation, MessageRole.ASSISTANT, answer)
            message.scope = scope_payload
            # Un documento puede borrarse mientras se genera la respuesta: su referencia se
            # guarda sin enlace (si no, la clave foránea impediría guardar la respuesta).
            existing = set(
                await session.scalars(
                    select(Document.id).where(
                        Document.id.in_({source.document_id for source, _ in shown})
                    )
                )
            )
            session.add_all(
                MessageSource(
                    message_id=message.id,
                    document_id=source.document_id if source.document_id in existing else None,
                    number=source.number,
                    filename=source.filename,
                    page=source.page,
                    heading=source.heading,
                    chunk_index=source.chunk_index,
                    score=source.score,
                    snippet=source.snippet,
                    cited=cited,
                )
                for source, cited in shown
            )
            await session.commit()
        yield sse_event("done", {"message_id": message.id})

    async def _detect_scope(self, question: str, query: str) -> list[ScopeDocument]:
        """Documentos que nombra la pregunta, si la detección está activada."""
        rag = self._config.rag
        if not rag.enabled or not rag.detect_document_names:
            return []
        async with self._sessionmaker() as session:
            documents = await indexed_documents(session)
        return detect_documents([question, query], documents)

    async def _search_query(self, prepared: PreparedChat) -> str:
        """Consulta para la búsqueda: la pregunta, reformulada si hay historial."""
        rag = self._config.rag
        if not rag.condense_question or self._retriever is None:
            return prepared.question
        # Sin documentos indexados no hay nada que buscar: se ahorra la llamada al modelo.
        if await self._retriever.is_empty():
            return prepared.question
        # El historial incluye la pregunta actual como último mensaje.
        previous = prepared.history[:-1][-rag.condense_history_messages :]
        return await condense_question(self._llm, previous, prepared.question)
