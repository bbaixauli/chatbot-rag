"""Orquestación de una petición de chat con respuesta en streaming (SSE)."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
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
from app.services.condense import build_condense_messages, condense_question
from app.services.conversations import ConversationRepository, make_title
from app.services.dashboard import QuestionRecord, record_question
from app.services.document_names import detect_documents
from app.services.llm import ChatMessage, LLMClient, LLMError, TokenUsage
from app.services.prompt_budget import (
    WINDOW_FULL_RATIO,
    PromptTooLargeError,
    PromptTrim,
    documents_budget,
    fit_recent,
    message_tokens,
    messages_tokens,
    server_trimmed,
    underestimated,
)
from app.services.prompts import PromptRepository
from app.services.rag import (
    ContextSource,
    Retrieval,
    Retriever,
    build_system_prompt,
    extract_citations,
)
from app.services.scope import (
    Scope,
    ScopeDocument,
    document_refs,
    indexed_documents,
    resolve_scope,
)

logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


TOO_LARGE_MESSAGE = (
    "La pregunta es demasiado larga para el modelo configurado. Acórtala o avisa al administrador."
)
RETRIEVAL_FAILED_MESSAGE = (
    "No se ha podido consultar la documentación: esta respuesta puede no basarse en los documentos."
)


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
    modelos pequeños), se muestran todas como «consultadas»; si se envió un documento
    completo, se muestra una sola vez (sus partes serían muchas fuentes del mismo fichero).
    """
    cited = set(extract_citations(answer, len(sources)))
    if cited:
        return [(s, True) for s in sources if s.number in cited]
    if sources and all(s.full_document for s in sources):
        return [(replace(sources[0], page=None, heading=None), False)]
    return [(s, False) for s in sources]


def source_payload(source: ContextSource, cited: bool) -> dict[str, Any]:
    return {
        "number": source.number,
        "document_id": source.document_id,
        "filename": source.filename,
        "title": source.name,
        "url": source.url,
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
    # Documentos y categorías a los que se limita la búsqueda (#58, #85).
    scope: Scope = field(default_factory=Scope)


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
        category_ids: Sequence[int] | None = None,
    ) -> PreparedChat:
        """Guarda el mensaje del usuario y prepara el prompt.

        Se ejecuta antes de empezar el streaming para poder responder 404 (conversación) o
        422 (`ScopeError`: algún documento o categoría no existe) si procede.
        `document_ids` y `category_ids` cambian la limitación de la búsqueda de la
        conversación (ver `resolve_scope`).
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
            scope = await resolve_scope(session, conversation, document_ids, category_ids)
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
        scope_payload: dict[str, Any] | None = None
        query: str | None = None
        scope_ids: list[str] = []
        if self._retriever:
            query = await self._search_query(prepared)
            record.search_query = query
            # La limitación elegida en el chat tiene prioridad; si no hay, se busca en los
            # documentos que nombre la pregunta (o su reformulación, en las de seguimiento).
            # Una categoría sin documentos indexados no limita la búsqueda.
            scope, origin = prepared.scope, ScopeSource.SELECTOR
            if not scope.document_ids:
                scope = Scope.of_documents(await self._detect_scope(prepared.question, query))
                origin = ScopeSource.DETECTED
            scope_ids = scope.document_ids
            if scope_ids:
                record.scope_document_ids = scope_ids
                record.scope_category_ids = [c.id for c in scope.categories] or None
                record.scope_source = origin.value
                scope_payload = scope.payload(origin.value)
                yield sse_event("scope", scope_payload)
        trim = PromptTrim()
        try:
            prompt, sources, retrieval_failed = await self._fit_prompt(
                prepared, query, scope_ids, trim
            )
        except PromptTooLargeError as exc:
            record.status = QuestionStatus.ERROR
            record.error_detail = str(exc)
            logger.warning(
                "Prompt demasiado grande en la conversación %s: %s",
                prepared.conversation_id,
                exc,
                extra={"conversation_id": prepared.conversation_id},
            )
            yield sse_event("error", {"detail": TOO_LARGE_MESSAGE})
            return
        record.sources_retrieved = len(sources)
        record.retrieval_failed = retrieval_failed
        if retrieval_failed:
            # Antes de la respuesta, para que se vea mientras se genera (#95).
            yield sse_event("retrieval_failed", {"detail": RETRIEVAL_FAILED_MESSAGE})
        record.prompt_tokens_estimated = messages_tokens(prompt)
        record.prompt_trim = trim.payload
        usage = TokenUsage()
        parts: list[str] = []
        try:
            async for delta in self._llm.stream_chat(prompt, usage=usage):
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

        self._check_server_count(prepared, record, trim, usage)
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
                "title": source.name,
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
            message.retrieval_failed = retrieval_failed
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

    async def _with_titles(self, sources: list[ContextSource]) -> list[ContextSource]:
        """Pone a las fuentes el título y la dirección web actuales de su documento (el
        índice no los tiene)."""
        if not sources:
            return sources
        async with self._sessionmaker() as session:
            refs = await document_refs(session, (s.document_id for s in sources))
        return [
            replace(s, title=ref.title, url=ref.url) if (ref := refs.get(s.document_id)) else s
            for s in sources
        ]

    async def _fit_prompt(
        self,
        prepared: PreparedChat,
        query: str | None,
        scope_ids: list[str],
        trim: PromptTrim,
    ) -> tuple[list[ChatMessage], list[ContextSource], bool]:
        """Prompt (sistema con la documentación, historial y pregunta), fuentes y si falló la
        búsqueda en la documentación (#95), dentro de la ventana del modelo si está
        configurada (ver `services/prompt_budget.py`)."""
        limit = self._config.llm.prompt_max_tokens
        failed = False
        if limit is None:
            sources: list[ContextSource] = []
            if self._retriever is not None and query is not None:
                retrieval = await self._retriever.retrieve_within(
                    query, scope_ids or None, questions=[prepared.question]
                )
                self._log_sections(prepared, retrieval)
                failed = retrieval.failed
                sources = await self._with_titles(retrieval.sources)
            system = ChatMessage("system", build_system_prompt(prepared.system_prompt, sources))
            return [system, *prepared.history], sources, failed

        *previous, question = prepared.history
        minimal = message_tokens(ChatMessage("system", prepared.system_prompt))
        minimal += message_tokens(question)
        if minimal > limit:
            raise PromptTooLargeError(minimal, limit)
        # 1-3: el último intercambio se conserva mientras quede sitio para él; la
        # documentación ocupa lo que quede, y el historial más antiguo, lo que sobre.
        last, older = previous[-2:], previous[:-2]
        if minimal + messages_tokens(last) > limit:
            trim.history += len(last)  # 4
            last = []
        sources = []
        if self._retriever is not None and query is not None:
            retrieval = await self._retriever.retrieve_within(
                query,
                scope_ids or None,
                documents_budget(limit, minimal + messages_tokens(last)),
                questions=[prepared.question],
            )
            self._log_sections(prepared, retrieval)
            failed = retrieval.failed
            trim.full_document = retrieval.full_document_skipped
            trim.sources = retrieval.dropped
            sources = await self._with_titles(retrieval.sources)

        def fixed(sources: list[ContextSource]) -> tuple[ChatMessage, int]:
            system = ChatMessage("system", build_system_prompt(prepared.system_prompt, sources))
            return system, message_tokens(system) + message_tokens(question) + messages_tokens(last)

        system, used = fixed(sources)
        # Por si los encabezados de las fuentes ocupan más de lo previsto.
        while sources and used > limit:
            sources = [replace(s, number=n) for n, s in enumerate(sources[:-1], start=1)]
            trim.sources += 1
            system, used = fixed(sources)
        kept, dropped = fit_recent(older, limit - used)
        trim.history += dropped
        prompt = [system, *kept, *last, question]
        if trim.payload:
            logger.warning(
                "Prompt ajustado a la ventana del modelo en la conversación %s: unos %d tokens "
                "de %d; recortes: %s",
                prepared.conversation_id,
                messages_tokens(prompt),
                limit,
                trim.payload,
                extra={"conversation_id": prepared.conversation_id},
            )
        return prompt, sources, failed

    @staticmethod
    def _log_sections(prepared: PreparedChat, retrieval: Retrieval) -> None:
        """Apartados citados en la pregunta y los que se han añadido completos (#74)."""
        if not retrieval.references:
            return
        logger.info(
            "Apartados citados en la conversación %s: %s; añadidos: %s",
            prepared.conversation_id,
            ", ".join(reference.text for reference in retrieval.references),
            ", ".join(f"{s.section} ({s.label})" for s in retrieval.located) or "ninguno",
            extra={"conversation_id": prepared.conversation_id},
        )

    def _check_server_count(
        self, prepared: PreparedChat, record: QuestionRecord, trim: PromptTrim, usage: TokenUsage
    ) -> None:
        """Guarda los tokens que contó el servidor y avisa si parece que recortó el prompt o
        si la estimación se quedó corta (#101)."""
        counted = usage.prompt_tokens
        record.prompt_tokens = counted
        estimated = record.prompt_tokens_estimated or 0
        llm = self._config.llm
        window = llm.context_window_tokens or None
        limit = llm.prompt_max_tokens
        trim.server = server_trimmed(estimated, counted, window)
        trim.underestimated = underestimated(counted, limit)
        if not (trim.server or trim.underestimated):
            return
        record.prompt_trim = trim.payload
        extra = {"conversation_id": prepared.conversation_id}
        if trim.underestimated:
            logger.warning(
                "El servidor del modelo ha contado %s tokens en un prompt estimado en unos %d: "
                "supera el límite de %d (ventana menos la reserva para la respuesta). La "
                "estimación se ha quedado corta; comprueba con `python -m app.diagnose tokens` "
                "si pasa a menudo con este modelo.",
                counted,
                estimated,
                limit,
                extra=extra,
            )
        if trim.server:
            if window and counted is not None and counted >= window * WINDOW_FULL_RATIO:
                reason = f"ha llenado la ventana de {window}"
            else:
                reason = "son bastantes menos de los estimados"
            logger.warning(
                "El servidor del modelo ha contado %s tokens en un prompt de unos %d (%s): "
                "probablemente lo ha recortado. Revisa la ventana de contexto del modelo "
                "(Configuración → Modelo de lenguaje).",
                counted,
                estimated,
                reason,
                extra=extra,
            )

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
        limit = self._config.llm.prompt_max_tokens
        if limit is not None:
            base = messages_tokens(build_condense_messages([], prepared.question))
            previous, _ = fit_recent(previous, limit - base)
        return await condense_question(self._llm, previous, prepared.question)
