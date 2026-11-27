"""Búsqueda limitada a unos documentos (#58) o categorías (#85).

Quien pregunta puede limitar la búsqueda a uno o varios documentos y a una o varias
categorías (el selector del chat). La limitación se guarda en la conversación
(`Conversation.document_ids` y `Conversation.category_ids`) y se aplica a sus preguntas
siguientes hasta que se cambie o se quite.

De las categorías se guarda la categoría, no sus documentos: en cada pregunta se busca en
sus documentos indexados de ese momento. La búsqueda se limita a la unión de los documentos
sueltos y los de las categorías.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Conversation, Document, DocumentStatus

# Documentos sueltos a los que se puede limitar una búsqueda a la vez (las categorías no
# tienen límite de documentos).
MAX_SCOPE_DOCUMENTS = 5


class ScopeError(Exception):
    """Algún documento o categoría de la limitación no existe."""


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


@dataclass(frozen=True, slots=True)
class ScopeCategory:
    """Categoría a cuyos documentos se limita la búsqueda."""

    id: int
    name: str

    @property
    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True, slots=True)
class Scope:
    """Limitación vigente: documentos sueltos, categorías y los documentos en que se busca."""

    documents: list[ScopeDocument] = field(default_factory=list)
    categories: list[ScopeCategory] = field(default_factory=list)
    # Documentos efectivos: los sueltos y los indexados de las categorías, sin repetir.
    document_ids: list[str] = field(default_factory=list)

    @classmethod
    def of_documents(cls, documents: Sequence[ScopeDocument]) -> "Scope":
        return cls(list(documents), [], [document.id for document in documents])

    def payload(self, source: str) -> dict[str, Any]:
        """El evento `scope` (también se guarda en el mensaje)."""
        return {
            "documents": [document.payload for document in self.documents],
            "categories": [category.payload for category in self.categories],
            "source": source,
        }


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


async def _existing_categories(
    session: AsyncSession, ids: Sequence[int]
) -> dict[int, ScopeCategory]:
    """Categorías que existen, por id."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Category.id, Category.name).where(Category.id.in_(set(ids)))
    )
    return {row[0]: ScopeCategory(*row) for row in rows.all()}


async def category_documents(session: AsyncSession, category_ids: Sequence[int]) -> list[str]:
    """Documentos indexados de las categorías, por título."""
    if not category_ids:
        return []
    rows = await session.scalars(
        select(Document.id)
        .where(
            Document.category_id.in_(set(category_ids)),
            Document.status == DocumentStatus.READY.value,
        )
        .order_by(Document.title, Document.id)
    )
    return list(rows)


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """Cómo se muestra y a dónde enlaza una referencia a un documento."""

    title: str
    # Dirección web de los documentos añadidos por ella (#78).
    url: str | None = None


async def document_refs(session: AsyncSession, ids: Iterable[str | None]) -> dict[str, DocumentRef]:
    """Título y dirección actuales de los documentos que existen, por id (las referencias
    muestran siempre los vigentes, #75)."""
    wanted = {document_id for document_id in ids if document_id}
    if not wanted:
        return {}
    rows = await session.execute(
        select(Document.id, Document.title, Document.source_url).where(Document.id.in_(wanted))
    )
    return {document_id: DocumentRef(title, url) for document_id, title, url in rows.all()}


async def category_names(session: AsyncSession, ids: Iterable[int]) -> dict[int, str]:
    """Nombre actual de las categorías que existen, por id."""
    return {
        category_id: category.name
        for category_id, category in (await _existing_categories(session, list(ids))).items()
    }


async def _documents(
    session: AsyncSession, conversation: Conversation, requested: Sequence[str] | None
) -> list[ScopeDocument]:
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


async def _categories(
    session: AsyncSession, conversation: Conversation, requested: Sequence[int] | None
) -> list[ScopeCategory]:
    if requested is not None:
        ids = list(dict.fromkeys(requested))
        found = await _existing_categories(session, ids)
        missing = [str(category_id) for category_id in ids if category_id not in found]
        if missing:
            raise ScopeError(f"Categorías no disponibles: {', '.join(missing)}")
        conversation.category_ids = ids or None
        return [found[category_id] for category_id in ids]

    stored = conversation.category_ids or []
    found = await _existing_categories(session, stored)
    kept = [category_id for category_id in stored if category_id in found]
    if kept != stored:
        conversation.category_ids = kept or None
    return [found[category_id] for category_id in kept]


async def resolve_scope(
    session: AsyncSession,
    conversation: Conversation,
    requested: Sequence[str] | None,
    requested_categories: Sequence[int] | None = None,
) -> Scope:
    """Aplica a la conversación la limitación pedida y devuelve la vigente.

    Para los documentos (`requested`) y las categorías (`requested_categories`):

    - `None`: se mantiene la guardada, sin los que se hayan borrado entretanto.
    - Una lista vacía: se quita.
    - Con elementos: se guarda; si alguno no existe se lanza `ScopeError`.

    Los documentos efectivos son los sueltos y los indexados de las categorías. Si no queda
    ninguno (p. ej. una categoría vacía), se busca en todos.
    """
    documents = await _documents(session, conversation, requested)
    categories = await _categories(session, conversation, requested_categories)
    ids = [document.id for document in documents]
    ids += await category_documents(session, [category.id for category in categories])
    return Scope(documents, categories, list(dict.fromkeys(ids)))


@dataclass(frozen=True, slots=True)
class SearchedIn:
    """Dónde se buscó una pregunta del registro (auditoría, #85)."""

    # `selector` o `detected` (ver `ScopeSource`).
    source: str
    # Nombre actual de las categorías elegidas.
    categories: list[str]
    # Título actual de los documentos, si no se eligieron categorías (son pocos).
    documents: list[str]
    document_count: int

    @property
    def label(self) -> str:
        """Descripción legible: «Recursos humanos (12 documentos)» o «NOR-013, Guía»."""
        text = ", ".join(self.categories or self.documents)
        if self.categories:
            text += f" ({self.document_count} documentos)"
        if self.source == "detected":
            text += " (nombrados en la pregunta)"
        return text


# Búsqueda registrada: (clave, documentos efectivos, categorías, origen).
SearchRecord = tuple[int, list[str] | None, list[int] | None, str | None]


async def searched_in(
    session: AsyncSession, searches: Sequence[SearchRecord]
) -> dict[int, SearchedIn]:
    """Dónde se limitó cada búsqueda registrada, por su clave (las que no se limitaron no
    aparecen). Con categorías, sus documentos no se nombran uno a uno: pueden ser muchos."""
    limited = [search for search in searches if search[1]]
    wanted = {
        document_id
        for _, documents, categories, _ in limited
        if not categories
        for document_id in documents or []
    }
    refs = await document_refs(session, wanted)
    chosen = {category_id for _, _, categories, _ in limited for category_id in categories or []}
    names = await category_names(session, chosen)
    result = {}
    for key, documents, categories, source in limited:
        ids = documents or []
        result[key] = SearchedIn(
            source=source or "selector",
            categories=[names.get(c, f"#{c}") for c in categories or []],
            documents=[] if categories else [refs[d].title if d in refs else d for d in ids],
            document_count=len(ids),
        )
    return result
