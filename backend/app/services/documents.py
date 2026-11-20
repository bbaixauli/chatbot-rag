"""Ciclo de vida de los documentos del RAG: subida, indexación, reindexación y borrado."""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_config import ConfigSource
from app.db.models import Document, DocumentKind, DocumentStatus, utcnow
from app.services.embeddings import EmbeddingError, Embeddings
from app.services.ingestion.chunker import chunk_sections
from app.services.ingestion.parsers import ParseError, kind_for_filename, parse_document
from app.services.tasks import INDEX_DOCUMENT, TaskRunner
from app.services.vectorstore import VectorStore, VectorStoreError

logger = logging.getLogger(__name__)

READ_CHUNK_BYTES = 1024 * 1024
MAX_FILENAME_LENGTH = 255


class UploadError(Exception):
    """Fichero rechazado (tipo no admitido, vacío o demasiado grande)."""


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    duplicate: bool


@dataclass(frozen=True, slots=True)
class IndexStats:
    documents: int
    chunks: int
    by_status: dict[str, int]
    # Documentos indexados con otro modelo de embeddings u otro troceado que el vigente.
    outdated: int


def _as_utc(moment: datetime) -> datetime:
    """SQLite devuelve las fechas sin zona horaria (guardadas en UTC) y otras bases de
    datos con ella: se normalizan para poder compararlas."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _request_version(requested_at: datetime) -> str:
    """Marca estable de una petición de indexación (la misma petición, la misma marca)."""
    return _as_utc(requested_at).strftime("%Y%m%dT%H%M%S%fZ")


def _next_request_time(previous: datetime) -> datetime:
    """Fecha de una petición nueva, siempre posterior a la anterior del documento.

    El reloj puede tener poca resolución (unos 15 ms en Windows): dos reindexaciones
    seguidas tendrían la misma fecha y, con ella, la misma marca, y la cola descartaría la
    segunda como duplicada.
    """
    return max(utcnow(), _as_utc(previous) + timedelta(microseconds=1))


def document_file_path(settings: Settings, document: Document) -> Path:
    """Ruta del original en disco (nombrado por id para evitar colisiones)."""
    return settings.uploads_dir / f"{document.id}{Path(document.filename).suffix.lower()}"


def safe_filename(filename: str) -> str:
    """Nombre visible del fichero sin rutas ni caracteres de control."""
    name = Path(filename.replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:MAX_FILENAME_LENGTH] or "documento"


class DocumentService:
    """Orquesta almacenamiento en disco, base de datos e índice vectorial."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        embeddings: Embeddings,
        vector_store: VectorStore,
        tasks: TaskRunner,
        config: ConfigSource,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._config = config
        self._embeddings = embeddings
        self._store = vector_store
        self._tasks = tasks
        tasks.register(INDEX_DOCUMENT, self.process)

    def file_path(self, document: Document) -> Path:
        """Ruta del original en disco (nombrado por id para evitar colisiones)."""
        return document_file_path(self._settings, document)

    async def upload(self, filename: str, content: AsyncReadable, uploaded_by: str) -> UploadResult:
        """Guarda el fichero, detecta duplicados y programa su indexación."""
        name = safe_filename(filename)
        kind = kind_for_filename(name)
        if kind is None:
            raise UploadError("Tipo de fichero no admitido (se aceptan PDF, Markdown y HTML)")

        uploads = self._settings.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        temp = uploads / f".subida-{uuid.uuid4()}"
        try:
            digest, size = await self._write(content, temp)
            if size == 0:
                raise UploadError("El fichero está vacío")
            async with self._sessionmaker() as session:
                existing = await session.scalar(select(Document).where(Document.sha256 == digest))
                if existing is not None:
                    return UploadResult(existing, duplicate=True)
                document = Document(
                    filename=name,
                    kind=kind.value,
                    size_bytes=size,
                    sha256=digest,
                    status=DocumentStatus.PENDING.value,
                    uploaded_by=uploaded_by,
                )
                session.add(document)
                await session.flush()
                temp.replace(self.file_path(document))
                await session.commit()
        finally:
            temp.unlink(missing_ok=True)

        await self._schedule(document.id, document.updated_at)
        return UploadResult(document, duplicate=False)

    async def _write(self, content: AsyncReadable, target: Path) -> tuple[str, int]:
        max_mb = self._config.current.indexing.upload_max_mb
        limit = max_mb * 1024 * 1024
        sha = hashlib.sha256()
        size = 0
        with target.open("wb") as out:
            while chunk := await content.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise UploadError(f"El fichero supera el tamaño máximo ({max_mb} MB)")
                sha.update(chunk)
                out.write(chunk)
        return sha.hexdigest(), size

    async def _schedule(self, document_id: str, requested_at: datetime) -> None:
        """Encola la indexación; `requested_at` (el `updated_at` que fijó la petición)
        distingue una petición nueva de la misma petición reprogramada."""
        await self._tasks.enqueue(INDEX_DOCUMENT, document_id, _request_version(requested_at))

    async def process(self, document_id: str) -> None:
        """Extrae, trocea, calcula embeddings e indexa un documento."""
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            document.status = DocumentStatus.PROCESSING.value
            document.error = None
            document.updated_at = utcnow()
            await session.commit()
            path, kind, filename = (
                self.file_path(document),
                DocumentKind(document.kind),
                document.filename,
            )

        chunk_count = 0
        error: str | None = None
        indexing = self._config.current.indexing
        model_name = self._embeddings.model_name
        try:
            sections = await asyncio.to_thread(parse_document, path, kind)
            chunks = chunk_sections(
                sections, indexing.chunk_size_chars, indexing.chunk_overlap_chars
            )
            vectors = await self._embeddings.embed([chunk.text for chunk in chunks])
            # Se sustituyen los fragmentos sin dejar el documento fuera del índice en ningún
            # momento (importante al reindexar): primero se escriben y luego se borran los
            # que sobran de la versión anterior.
            await self._store.add(document_id, filename, chunks, vectors)
            await self._store.delete_chunks_from(document_id, len(chunks))
            chunk_count = len(chunks)
        except (ParseError, EmbeddingError, VectorStoreError) as exc:
            error = str(exc)
        except Exception:
            logger.exception("Error inesperado indexando %s", document_id)
            error = "Error inesperado al procesar el documento"

        if error:
            await self._store.delete_document(document_id)
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:  # borrado mientras se procesaba
                await self._store.delete_document(document_id)
                return
            document.status = (DocumentStatus.ERROR if error else DocumentStatus.READY).value
            document.error = error
            document.chunk_count = chunk_count
            document.embedding_model = None if error else model_name
            document.chunk_size_chars = None if error else indexing.chunk_size_chars
            document.chunk_overlap_chars = None if error else indexing.chunk_overlap_chars
            document.updated_at = utcnow()
            await session.commit()

    async def list(self) -> list[Document]:
        async with self._sessionmaker() as session:
            result = await session.scalars(select(Document).order_by(Document.created_at.desc()))
            return list(result)

    async def get(self, document_id: str) -> Document | None:
        async with self._sessionmaker() as session:
            return await session.get(Document, document_id)

    async def delete(self, document_id: str) -> bool:
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return False
            path = self.file_path(document)
            await session.delete(document)
            await session.commit()
        await self._store.delete_document(document_id)
        path.unlink(missing_ok=True)
        return True

    async def reindex(self, document_id: str) -> Document | None:
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return None
            document.status = DocumentStatus.PENDING.value
            document.error = None
            document.updated_at = _next_request_time(document.updated_at)
            await session.commit()
        await self._schedule(document_id, document.updated_at)
        return document

    async def reindex_all(self) -> int:
        """Vacía el índice y vuelve a indexar todos los documentos.

        Vaciarlo antes es imprescindible tras cambiar el modelo de embeddings (cambia la
        dimensión de los vectores) y evita que varios workers lo hagan a la vez. Hasta que
        terminen, el chat responde sin esos documentos.
        """
        await self._store.reset()
        documents = await self.list()
        for document in documents:
            await self.reindex(document.id)
        return len(documents)

    async def resume_pending(self) -> int:
        """Reprograma documentos que quedaron a medias (p. ej. por un reinicio)."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Document.id, Document.updated_at).where(
                    Document.status.in_(
                        [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]
                    )
                )
            )
            rows = result.all()
        # Los pendientes conservan la fecha de su petición: con la cola de arq se reconoce
        # el trabajo ya encolado y no se duplica.
        for document_id, updated_at in rows:
            await self._schedule(document_id, updated_at)
        return len(rows)

    async def indexing_in_progress(self) -> int:
        """Documentos pendientes o indexándose."""
        async with self._sessionmaker() as session:
            count = await session.scalar(
                select(func.count()).where(
                    Document.status.in_(
                        [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]
                    )
                )
            )
        return int(count or 0)

    async def stats(self) -> IndexStats:
        indexing = self._config.current.indexing
        model_name = self._embeddings.model_name
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(
                    Document.status, func.count(), func.coalesce(func.sum(Document.chunk_count), 0)
                ).group_by(Document.status)
            )
            by_status = {}
            chunks = 0
            for status, count, chunk_sum in rows.all():
                by_status[status] = int(count)
                if status == DocumentStatus.READY.value:
                    chunks += int(chunk_sum)
            # Sin troceado guardado (indexados antes de guardarlo) solo se compara el modelo.
            outdated = await session.scalar(
                select(func.count()).where(
                    Document.status == DocumentStatus.READY.value,
                    or_(
                        Document.embedding_model.is_distinct_from(model_name),
                        Document.chunk_size_chars != indexing.chunk_size_chars,
                        Document.chunk_overlap_chars != indexing.chunk_overlap_chars,
                    ),
                )
            )
        return IndexStats(
            documents=sum(by_status.values()),
            chunks=chunks,
            by_status=by_status,
            outdated=int(outdated or 0),
        )
