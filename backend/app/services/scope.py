"""Búsqueda limitada a unos documentos (#58).

Quien pregunta puede limitar la búsqueda a uno o varios documentos (el selector del chat).
La limitación se guarda en la conversación (`Conversation.document_ids`) y se aplica a sus
preguntas siguientes hasta que se cambie o se quite.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Document, DocumentStatus

# Documentos a los que se puede limitar una búsqueda a la vez.
MAX_SCOPE_DOCUMENTS = 5


class ScopeError(Exception):
    """Algún documento de la limitación no existe."""


@dataclass(frozen=True, slots=True)
class ScopeDocument:
    """Documento al que se limita la búsqueda."""

    id: str
    filename: str
    title: str = ""

    @property
    def payload(self) -> dict[str, str]:
        """Como se envía en el evento `scope` y se guarda en el mensaje."""
        return {"id": self.id, "filename": self.filename, "title": self.title or self.filename}


async def indexed_documents(session: AsyncSession) -> list[ScopeDocument]:
    """Documentos indexados (los que se pueden nombrar en una pregunta)."""
    rows = await session.execute(
        select(Document.id, Document.filename, Document.title).where(
            Document.status == DocumentStatus.READY.value
        )
    )
    return [ScopeDocument(*row) for row in rows.all()]


async def _existing(session: AsyncSession, ids: Sequence[str]) -> dict[str, ScopeDocument]:
    """Documentos que existen, por id."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Document.id, Document.filename, Document.title).where(Document.id.in_(set(ids)))
    )
    return {row[0]: ScopeDocument(*row) for row in rows.all()}


async def document_titles(session: AsyncSession, ids: Iterable[str | None]) -> dict[str, str]:
    """Título actual de los documentos que existen, por id (las referencias muestran
    siempre el título vigente, #75)."""
    wanted = {document_id for document_id in ids if document_id}
    if not wanted:
        return {}
    rows = await session.execute(select(Document.id, Document.title).where(Document.id.in_(wanted)))
    return {document_id: title for document_id, title in rows.all()}


async def resolve_scope(
    session: AsyncSession, conversation: Conversation, requested: Sequence[str] | None
) -> list[ScopeDocument]:
    """Aplica a la conversación la limitación pedida y devuelve la vigente.

    - `requested` es `None`: se mantiene la guardada, sin los documentos que se hayan
      borrado entretanto (si no queda ninguno, se busca en todos).
    - `requested` es una lista vacía: se quita la limitación.
    - Con documentos: se guarda; si alguno no existe se lanza `ScopeError`.
    """
    if requested is not None:
        ids = list(dict.fromkeys(requested))
        names = await _existing(session, ids)
        missing = [document_id for document_id in ids if document_id not in names]
        if missing:
            raise ScopeError(f"Documentos no disponibles: {', '.join(missing)}")
        conversation.document_ids = ids or None
        return [names[document_id] for document_id in ids]

    stored = conversation.document_ids or []
    names = await _existing(session, stored)
    kept = [document_id for document_id in stored if document_id in names]
    if kept != stored:
        conversation.document_ids = kept or None
    return [names[document_id] for document_id in kept]
