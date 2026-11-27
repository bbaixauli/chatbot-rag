"""Ciclo de vida de los documentos del RAG: subida, indexación, reindexación y borrado."""

import asyncio
import hashlib
import logging
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_config import ConfigSource, IndexingConfig
from app.db.models import Category, Document, DocumentKind, DocumentStatus, utcnow
from app.services.archives import ArchiveError, MemberReader, members, open_archive
from app.services.embeddings import EmbeddingError, Embeddings
from app.services.ingestion.parsers import ParseError, kind_for_filename, parse_document
from app.services.ingestion.preprocess import (
    build_chunks,
    document_label,
    document_title,
    embedding_texts,
    preprocessing_signature,
)
from app.services.ingestion.titles import extract_title
from app.services.tasks import INDEX_DOCUMENT, REFRESH_DOCUMENT, TaskRunner
from app.services.vectorstore import VectorStore, VectorStoreError
from app.services.web import (
    Download,
    NotFoundError,
    WebError,
    WebFetcher,
    check_url,
    decode_html,
    document_kind,
    document_name,
    html_to_markdown,
    page_title,
)

logger = logging.getLogger(__name__)

READ_CHUNK_BYTES = 1024 * 1024
MAX_FILENAME_LENGTH = 255


class UploadError(Exception):
    """Fichero rechazado (tipo no admitido, vacío o demasiado grande)."""


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class BytesReader:
    """Lector asíncrono de un contenido que ya está en memoria."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)

    async def read(self, size: int = -1) -> bytes:
        size = len(self._data) if size < 0 else size
        chunk, self._data = self._data[:size], self._data[size:]
        return bytes(chunk)


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    duplicate: bool


@dataclass(frozen=True, slots=True)
class Source:
    """Origen de un documento añadido por su dirección web (#78)."""

    url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class ArchiveEntryResult:
    """Resultado de un fichero de un ZIP: el documento subido o por qué no se subió."""

    # Ruta dentro del ZIP.
    name: str
    result: UploadResult | None = None
    error: str | None = None


def is_archive(filename: str) -> bool:
    return Path(filename).suffix.lower() == ".zip"


@dataclass(frozen=True, slots=True)
class IndexStats:
    documents: int
    chunks: int
    by_status: dict[str, int]
    # Documentos indexados con otro modelo de embeddings, otro troceado, otro preprocesado
    # u otro título o categoría que los vigentes.
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


async def _content(download: Download) -> tuple[DocumentKind, str, bytes, str | None]:
    """Tipo, nombre, contenido que se guarda y título de un documento descargado: de las
    páginas web, su contenido principal en Markdown y el título de la página."""
    kind = document_kind(download)
    name = safe_filename(document_name(download, kind))
    if kind is not DocumentKind.WEB:
        return kind, name, download.body, None
    html = decode_html(download)
    body = (await asyncio.to_thread(html_to_markdown, html)).encode("utf-8")
    return kind, name, body, await asyncio.to_thread(page_title, html)


def document_file_path(settings: Settings, document: Document) -> Path:
    """Ruta del original en disco (nombrado por id para evitar colisiones). De las páginas
    web se guarda su contenido limpio, en Markdown."""
    suffix = ".md" if document.kind == DocumentKind.WEB else Path(document.filename).suffix
    return settings.uploads_dir / f"{document.id}{suffix.lower()}"


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
        fetcher: WebFetcher | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        # Descargas por dirección web; se puede sustituir (tests).
        self.fetcher = fetcher or WebFetcher()
        self._settings = settings
        self._config = config
        self._embeddings = embeddings
        self._store = vector_store
        self._tasks = tasks
        tasks.register(INDEX_DOCUMENT, self.process)
        tasks.register(REFRESH_DOCUMENT, self.refresh)

    def file_path(self, document: Document) -> Path:
        """Ruta del original en disco (nombrado por id para evitar colisiones)."""
        return document_file_path(self._settings, document)

    async def upload(
        self,
        filename: str,
        content: AsyncReadable,
        uploaded_by: str,
        *,
        use_filename: bool = False,
        category_id: int | None = None,
    ) -> UploadResult:
        """Guarda el fichero, detecta duplicados y programa su indexación.

        El título es el de los metadatos del fichero o, si no tiene uno válido o se pide
        con `use_filename`, el nombre del fichero. Un duplicado se devuelve tal cual (sin
        cambiar su título ni su categoría).
        """
        name = safe_filename(filename)
        kind = kind_for_filename(name)
        if kind is None:
            raise UploadError(
                "Tipo de fichero no admitido (se aceptan PDF, Markdown, HTML, Word, PowerPoint, "
                "Excel y OpenDocument)"
            )
        return await self._save(
            name, kind, content, uploaded_by, use_filename=use_filename, category_id=category_id
        )

    async def add_url(
        self,
        url: str,
        uploaded_by: str,
        *,
        use_filename: bool = False,
        category_id: int | None = None,
    ) -> UploadResult:
        """Descarga un documento por su dirección y lo da de alta (como `upload`).

        Las páginas HTML se guardan reducidas a su contenido principal, en Markdown. Si la
        dirección ya se había añadido, se devuelve ese documento como duplicado.
        """
        config = self._config.current
        try:
            url = check_url(url, config.web)
        except WebError as exc:
            raise UploadError(str(exc)) from exc
        async with self._sessionmaker() as session:
            existing = await session.scalar(select(Document).where(Document.source_url == url))
        if existing is not None:
            return UploadResult(existing, duplicate=True)
        max_bytes = config.indexing.upload_max_mb * 1024 * 1024
        try:
            download = await self.fetcher.fetch(url, config.web, max_bytes, tls=config.tls)
            kind, name, body, title = await _content(download)
        except WebError as exc:
            raise UploadError(str(exc)) from exc
        return await self._save(
            name,
            kind,
            BytesReader(body),
            uploaded_by,
            use_filename=use_filename,
            category_id=category_id,
            title=title,
            source=Source(url, download.etag, download.last_modified),
        )

    async def check_now(self, document_id: str) -> Document | None:
        """Comprueba ahora un documento web («Comprobar ahora») y lo devuelve actualizado.

        Devuelve `None` si no existe (o se ha borrado por no encontrarse). Lanza
        `UploadError` si no se añadió por su dirección.
        """
        document = await self.get(document_id)
        if document is None:
            return None
        if document.source_url is None:
            raise UploadError("El documento no se añadió por su dirección web")
        await self.refresh(document_id)
        return await self.get(document_id)

    async def refresh(self, document_id: str) -> None:
        """Comprueba si un documento web ha cambiado y, si es así, lo actualiza (#79).

        - Sin cambios (304 o el mismo contenido): se anota la comprobación.
        - Con cambios: se sustituye el contenido y se reindexa (mismo documento, así que las
          referencias y las búsquedas limitadas a él siguen valiendo).
        - «No encontrado» (404 o 410): se cuenta; al llegar al límite configurado, se borra.
        - Otros errores (red, 5xx…): se anotan, sin contar ni cambiar el documento.

        Si el documento se está indexando, se deja para la siguiente comprobación.
        """
        document = await self.get(document_id)
        if document is None or document.source_url is None:
            return
        if document.status in (DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value):
            return
        config = self._config.current
        headers = {}
        if document.source_etag:
            headers["If-None-Match"] = document.source_etag
        if document.source_last_modified:
            headers["If-Modified-Since"] = document.source_last_modified
        max_bytes = config.indexing.upload_max_mb * 1024 * 1024
        try:
            download = await self.fetcher.fetch(
                document.source_url, config.web, max_bytes, headers, tls=config.tls
            )
            if download.not_modified:
                await self._checked(document_id, download)
                return
            kind, _, body, _ = await _content(download)
        except NotFoundError as exc:
            await self._not_found(document_id, str(exc), config.web.not_found_limit)
            return
        except WebError as exc:
            await self._checked(document_id, error=str(exc))
            return
        if kind.value != document.kind:
            await self._checked(
                document_id,
                download,
                error=f"El tipo del documento ha cambiado ({document.kind} → {kind.value})",
            )
            return
        digest = hashlib.sha256(body).hexdigest()
        if digest == document.sha256:
            await self._checked(document_id, download)
            return
        await self._replace(document_id, download, body, digest)

    async def _checked(
        self,
        document_id: str,
        download: Download | None = None,
        *,
        error: str | None = None,
    ) -> None:
        """Anota una comprobación sin cambios (con la respuesta) o con un error."""
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            document.source_checked_at = utcnow()
            document.source_error = error
            if download is not None:
                document.source_not_found = 0
                document.source_etag = download.etag or document.source_etag
                document.source_last_modified = (
                    download.last_modified or document.source_last_modified
                )
            await session.commit()

    async def _not_found(self, document_id: str, error: str, limit: int) -> None:
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            document.source_checked_at = utcnow()
            document.source_not_found += 1
            document.source_error = error
            remove = document.source_not_found >= limit
            url, times = document.source_url, document.source_not_found
            await session.commit()
        if remove:
            logger.info(
                "Documento web borrado: no se encuentra tras %d comprobaciones (%s)", times, url
            )
            await self.delete(document_id)

    async def _replace(
        self, document_id: str, download: Download, body: bytes, digest: str
    ) -> None:
        """Sustituye el contenido de un documento web y programa su reindexación."""
        uploads = self._settings.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        temp = uploads / f".actualizacion-{uuid.uuid4()}"
        try:
            await asyncio.to_thread(temp.write_bytes, body)
            async with self._sessionmaker() as session:
                document = await session.get(Document, document_id)
                if document is None:
                    return
                same = await session.scalar(
                    select(Document.title).where(
                        Document.sha256 == digest, Document.id != document_id
                    )
                )
                now = utcnow()
                document.source_checked_at = now
                if same is not None:
                    # `sha256` es único: se conserva la versión anterior.
                    document.source_error = f"El contenido nuevo es el mismo que el de «{same}»"
                    await session.commit()
                    return
                document.sha256 = digest
                document.size_bytes = len(body)
                document.status = DocumentStatus.PENDING.value
                document.error = None
                document.updated_at = _next_request_time(document.updated_at)
                document.source_changed_at = now
                document.source_error = None
                document.source_not_found = 0
                document.source_etag = download.etag
                document.source_last_modified = download.last_modified
                try:
                    # Antes de sustituir el fichero: si otro documento ha recibido el mismo
                    # contenido entre la consulta y aquí, se conserva la versión anterior.
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    await self._duplicate_content(document_id, digest, now)
                    return
                temp.replace(self.file_path(document))
                await session.commit()
                requested_at = document.updated_at
        finally:
            temp.unlink(missing_ok=True)
        logger.info("Documento web actualizado: %s", download.url)
        await self._schedule(document_id, requested_at)

    async def _duplicate_content(self, document_id: str, digest: str, checked_at: datetime) -> None:
        """Anota que el contenido nuevo de un documento web ya es el de otro documento
        (guardado a la vez que se comprobaba este)."""
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            same = await session.scalar(select(Document.title).where(Document.sha256 == digest))
            document.source_checked_at = checked_at
            document.source_error = (
                f"El contenido nuevo es el mismo que el de «{same}»"
                if same
                else "El contenido nuevo es el mismo que el de otro documento"
            )
            await session.commit()

    async def _save(
        self,
        name: str,
        kind: DocumentKind,
        content: AsyncReadable,
        uploaded_by: str,
        *,
        use_filename: bool,
        category_id: int | None,
        title: str | None = None,
        source: Source | None = None,
    ) -> UploadResult:
        """Guarda el contenido, detecta duplicados y programa la indexación. Sin `title`,
        se toma de los metadatos del fichero."""
        uploads = self._settings.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        temp = uploads / f".subida-{uuid.uuid4()}"
        try:
            digest, size = await self._write(content, temp)
            if size == 0:
                raise UploadError("El fichero está vacío")
            if use_filename:
                title = None
            elif title is None:
                title = await asyncio.to_thread(extract_title, temp, kind)
            async with self._sessionmaker() as session:
                existing = await session.scalar(select(Document).where(Document.sha256 == digest))
                if existing is not None:
                    return UploadResult(existing, duplicate=True)
                document = Document(
                    filename=name,
                    title=title or name,
                    category_id=category_id,
                    kind=kind.value,
                    size_bytes=size,
                    sha256=digest,
                    status=DocumentStatus.PENDING.value,
                    uploaded_by=uploaded_by,
                    source_url=source.url if source else None,
                    source_etag=source.etag if source else None,
                    source_last_modified=source.last_modified if source else None,
                    # Recién descargado: comprobado y cambiado ahora (#79).
                    source_checked_at=utcnow() if source else None,
                    source_changed_at=utcnow() if source else None,
                )
                session.add(document)
                try:
                    await session.flush()
                except IntegrityError:
                    # Otra subida simultánea con el mismo contenido (p. ej. dos direcciones
                    # del mismo lote, #72) se ha guardado entre la consulta y el alta.
                    await session.rollback()
                    existing = await session.scalar(
                        select(Document).where(Document.sha256 == digest)
                    )
                    if existing is None:
                        raise
                    return UploadResult(existing, duplicate=True)
                temp.replace(self.file_path(document))
                await session.commit()
        finally:
            temp.unlink(missing_ok=True)

        await self._schedule(document.id, document.updated_at)
        return UploadResult(document, duplicate=False)

    async def upload_archive(
        self,
        filename: str,
        content: AsyncReadable,
        uploaded_by: str,
        *,
        use_filename: bool = False,
        category_id: int | None = None,
    ) -> list[ArchiveEntryResult]:
        """Da de alta cada fichero de un ZIP como un documento (como `upload`).

        El ZIP no se conserva ni tiene límite de tamaño propio: el límite de subida se
        aplica a cada fichero. Lanza `UploadError` si el ZIP no se puede leer o está vacío.
        """
        uploads = self._settings.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        temp = uploads / f".zip-{uuid.uuid4()}"
        try:
            await self._write(content, temp, limited=False)
            try:
                archive = open_archive(str(temp))
            except ArchiveError as exc:
                raise UploadError(str(exc)) from exc
            results: list[ArchiveEntryResult] = []
            with archive:
                for member in members(archive):
                    if member.info is None:
                        results.append(ArchiveEntryResult(member.name, error=member.error))
                        continue
                    try:
                        with archive.open(member.info) as stream:
                            result = await self.upload(
                                PurePosixPath(member.name).name,
                                MemberReader(stream),
                                uploaded_by,
                                use_filename=use_filename,
                                category_id=category_id,
                            )
                    except (UploadError, ArchiveError) as exc:
                        results.append(ArchiveEntryResult(member.name, error=str(exc)))
                    except zipfile.BadZipFile:
                        results.append(
                            ArchiveEntryResult(
                                member.name, error="El fichero está dañado dentro del ZIP"
                            )
                        )
                    except NotImplementedError:
                        # Métodos de compresión que Python no lee (p. ej. Deflate64).
                        results.append(
                            ArchiveEntryResult(
                                member.name,
                                error="Método de compresión no admitido: vuelve a crear el ZIP",
                            )
                        )
                    else:
                        results.append(ArchiveEntryResult(member.name, result=result))
            if not results:
                raise UploadError("El ZIP no contiene ningún fichero")
            return results
        finally:
            temp.unlink(missing_ok=True)

    async def _write(
        self, content: AsyncReadable, target: Path, *, limited: bool = True
    ) -> tuple[str, int]:
        """Copia el contenido a `target` y devuelve su SHA-256 y su tamaño. Con `limited`,
        falla si supera el tamaño máximo de subida."""
        max_mb = self._config.current.indexing.upload_max_mb
        limit = max_mb * 1024 * 1024 if limited else None
        sha = hashlib.sha256()
        size = 0
        with target.open("wb") as out:
            while chunk := await content.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if limit is not None and size > limit:
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
            path, kind, filename, title = (
                self.file_path(document),
                DocumentKind(document.kind),
                document.filename,
                document.title,
            )
            category = (
                await session.scalar(
                    select(Category.name).where(Category.id == document.category_id)
                )
                if document.category_id is not None
                else None
            )

        chunk_count = 0
        error: str | None = None
        indexing = self._config.current.indexing
        model_name = self._embeddings.model_name
        try:
            sections = await asyncio.to_thread(parse_document, path, kind)
            chunks = build_chunks(sections, indexing, kind)
            texts = embedding_texts(
                chunks,
                document_label(title, filename),
                indexing,
                category,
                document_title(filename),
            )
            vectors = await self._embeddings.embed(texts)
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
            document.preprocessing = None if error else preprocessing_signature(indexing, kind)
            document.indexed_title = None if error else title
            document.indexed_category = None if error else category
            document.updated_at = utcnow()
            await session.commit()

    async def list(self) -> list[Document]:
        async with self._sessionmaker() as session:
            result = await session.scalars(select(Document).order_by(Document.created_at.desc()))
            return list(result)

    async def category_exists(self, category_id: int) -> bool:
        async with self._sessionmaker() as session:
            return await session.get(Category, category_id) is not None

    async def get(self, document_id: str) -> Document | None:
        async with self._sessionmaker() as session:
            return await session.get(Document, document_id)

    async def update(
        self, document_id: str, changes: dict[str, str | int | None]
    ) -> Document | None:
        """Cambia el título (`title`) o la categoría (`category_id`, nula: sin categoría).

        No reindexa: si el título o la categoría van en el texto de los vectores, el
        documento queda desactualizado hasta que se reindexe.
        """
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return None
            if "title" in changes:
                document.title = str(changes["title"])
            if "category_id" in changes:
                category_id = changes["category_id"]
                document.category_id = None if category_id is None else int(category_id)
            await session.commit()
            return document

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

    async def reindex_outdated(self) -> int:
        """Reindexa los documentos desactualizados (sin vaciar el índice)."""
        ids = await self.outdated_ids()
        for document_id in sorted(ids):
            await self.reindex(document_id)
        return len(ids)

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

    def _outdated(self, indexing: IndexingConfig, model_name: str) -> ColumnElement[bool]:
        """Condición de los documentos indexados que hay que reindexar (hay que unir
        `Category`).

        Sin troceado guardado (indexados antes de guardarlo) no se compara el troceado; sin
        preprocesado guardado (indexados antes de #62), sí cuentan como desactualizados: no
        tienen marcados los fragmentos de índice. El título y la categoría (#75) solo
        cuentan si van en el texto de los vectores.
        """
        changed = [
            Document.embedding_model.is_distinct_from(model_name),
            Document.chunk_size_chars != indexing.chunk_size_chars,
            Document.chunk_overlap_chars != indexing.chunk_overlap_chars,
            Document.preprocessing.is_distinct_from(
                case(
                    (
                        Document.kind == DocumentKind.PDF.value,
                        preprocessing_signature(indexing, DocumentKind.PDF.value),
                    ),
                    else_=preprocessing_signature(indexing),
                )
            ),
        ]
        if indexing.context_prefix:
            changed += [
                Document.indexed_title.is_distinct_from(Document.title),
                Document.indexed_category.is_distinct_from(Category.name),
            ]
        return and_(Document.status == DocumentStatus.READY.value, or_(*changed))

    async def outdated_ids(self) -> set[str]:
        """Documentos indexados que hay que reindexar."""
        indexing = self._config.current.indexing
        async with self._sessionmaker() as session:
            result = await session.scalars(
                select(Document.id)
                .outerjoin(Category, Document.category_id == Category.id)
                .where(self._outdated(indexing, self._embeddings.model_name))
            )
            return set(result)

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
            outdated = await session.scalar(
                select(func.count())
                .select_from(Document)
                .outerjoin(Category, Document.category_id == Category.id)
                .where(self._outdated(indexing, model_name))
            )
        return IndexStats(
            documents=sum(by_status.values()),
            chunks=chunks,
            by_status=by_status,
            outdated=int(outdated or 0),
        )
