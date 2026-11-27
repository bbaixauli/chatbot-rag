"""Endpoints de administración de documentos del RAG."""

import asyncio
import logging
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import PublicDocumentOut
from app.core.auth import AdminDep, CurrentUserDep
from app.db.models import Category, Document, DocumentKind, DocumentStatus
from app.services.documents import DocumentService, UploadError, is_archive
from app.services.web import MAX_URL_LENGTH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/documents", tags=["documents"])

# Direcciones que se pueden añadir de una vez y descargas simultáneas.
MAX_URLS = 50
URL_CONCURRENCY = 4


def get_documents(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.documents
    return service


DocumentsDep = Annotated[DocumentService, Depends(get_documents)]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    title: str
    category_id: int | None
    kind: DocumentKind
    size_bytes: int
    status: Literal["pending", "processing", "ready", "error"]
    chunk_count: int
    error: str | None
    embedding_model: str | None
    chunk_size_chars: int | None
    chunk_overlap_chars: int | None
    uploaded_by: str
    created_at: datetime
    updated_at: datetime
    # Dirección web, si se añadió por ella (#78), y su actualización (#79): última
    # comprobación, último cambio, error de la última comprobación y comprobaciones seguidas
    # con «no encontrado».
    source_url: str | None = None
    source_checked_at: datetime | None = None
    source_changed_at: datetime | None = None
    source_error: str | None = None
    source_not_found: int = 0
    # Indexado con otro modelo, troceado, preprocesado, título o categoría: hay que
    # reindexarlo.
    outdated: bool = False


class DocumentUpdate(BaseModel):
    """Cambia el título o la categoría (`null`: sin categoría)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    category_id: int | None = None


class UrlsIn(BaseModel):
    """Direcciones web que se añaden como documentos (#78)."""

    urls: list[Annotated[str, Field(min_length=1, max_length=MAX_URL_LENGTH)]] = Field(
        min_length=1, max_length=MAX_URLS
    )
    use_filename: bool = False
    category_id: int | None = None


class UploadResultOut(BaseModel):
    # En un ZIP, la ruta del fichero dentro de él.
    filename: str
    # ZIP del que viene el fichero (#77).
    archive: str | None = None
    document: DocumentOut | None = None
    duplicate: bool = False
    error: str | None = None


class StatsOut(BaseModel):
    documents: int
    chunks: int
    by_status: dict[str, int]
    embedding_model: str
    # Indexados con otro modelo de embeddings, otro troceado, otro preprocesado u otro
    # título o categoría: hay que reindexarlos.
    outdated: int


class ReindexAllOut(BaseModel):
    scheduled: int


async def _get_or_404(service: DocumentService, document_id: str) -> Document:
    document = await service.get(document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return document


async def _document_out(service: DocumentService, document: Document) -> DocumentOut:
    out = DocumentOut.model_validate(document)
    out.outdated = document.id in await service.outdated_ids()
    return out


@router.get("", response_model=list[DocumentOut])
async def list_documents(_: AdminDep, service: DocumentsDep) -> list[DocumentOut]:
    outdated = await service.outdated_ids()
    return [
        DocumentOut.model_validate(document).model_copy(
            update={"outdated": document.id in outdated}
        )
        for document in await service.list()
    ]


async def _check_category(service: DocumentService, category_id: int | None) -> None:
    if category_id is not None and not await service.category_exists(category_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "La categoría no existe")


@router.post("", response_model=list[UploadResultOut])
async def upload_documents(
    files: list[UploadFile],
    admin: AdminDep,
    service: DocumentsDep,
    use_filename: Annotated[bool, Form()] = False,
    category_id: Annotated[int | None, Form()] = None,
) -> list[UploadResultOut]:
    """Sube uno o varios ficheros. Cada uno se valida y procesa por separado.

    Un ZIP da de alta cada fichero admitido que contenga (un resultado por fichero, con
    `archive`). Todos van a la categoría `category_id` (si se indica) y, con
    `use_filename`, toman como título el nombre del fichero en lugar del de sus metadatos.
    """
    await _check_category(service, category_id)
    results: list[UploadResultOut] = []
    for file in files:
        filename = file.filename or "documento"
        try:
            if is_archive(filename):
                entries = await service.upload_archive(
                    filename,
                    file,
                    admin.username,
                    use_filename=use_filename,
                    category_id=category_id,
                )
                results.extend(
                    UploadResultOut(
                        filename=entry.name,
                        archive=filename,
                        document=(
                            DocumentOut.model_validate(entry.result.document)
                            if entry.result
                            else None
                        ),
                        duplicate=bool(entry.result and entry.result.duplicate),
                        error=entry.error,
                    )
                    for entry in entries
                )
                continue
            result = await service.upload(
                filename,
                file,
                admin.username,
                use_filename=use_filename,
                category_id=category_id,
            )
        except UploadError as exc:
            results.append(UploadResultOut(filename=filename, error=str(exc)))
            continue
        finally:
            await file.close()
        results.append(
            UploadResultOut(
                filename=filename,
                document=DocumentOut.model_validate(result.document),
                duplicate=result.duplicate,
            )
        )
    return results


@router.post("/urls", response_model=list[UploadResultOut])
async def add_urls(body: UrlsIn, admin: AdminDep, service: DocumentsDep) -> list[UploadResultOut]:
    """Descarga y da de alta los documentos de varias direcciones web (un resultado por
    dirección, en el mismo orden; `filename` es la dirección)."""
    await _check_category(service, body.category_id)
    semaphore = asyncio.Semaphore(URL_CONCURRENCY)

    async def add(url: str) -> UploadResultOut:
        async with semaphore:
            try:
                result = await service.add_url(
                    url,
                    admin.username,
                    use_filename=body.use_filename,
                    category_id=body.category_id,
                )
            except UploadError as exc:
                return UploadResultOut(filename=url, error=str(exc))
            except Exception:
                # Un fallo imprevisto con una dirección no debe perder el resultado de las
                # demás, que ya se han dado de alta.
                logger.exception("Error inesperado al añadir %s", url)
                return UploadResultOut(
                    filename=url, error="Error inesperado al añadir la dirección"
                )
        return UploadResultOut(
            filename=url,
            document=DocumentOut.model_validate(result.document),
            duplicate=result.duplicate,
        )

    urls = list(dict.fromkeys(url.strip() for url in body.urls if url.strip()))
    return list(await asyncio.gather(*(add(url) for url in urls)))


@router.get("/stats", response_model=StatsOut)
async def index_stats(_: AdminDep, service: DocumentsDep, request: Request) -> StatsOut:
    stats = await service.stats()
    return StatsOut(
        documents=stats.documents,
        chunks=stats.chunks,
        by_status=stats.by_status,
        embedding_model=request.app.state.embeddings.model_name,
        outdated=stats.outdated,
    )


@router.post("/reindex", response_model=ReindexAllOut, status_code=status.HTTP_202_ACCEPTED)
async def reindex_all(_: AdminDep, service: DocumentsDep) -> ReindexAllOut:
    """Reindexa todo (necesario si cambia el modelo de embeddings)."""
    return ReindexAllOut(scheduled=await service.reindex_all())


@router.post(
    "/reindex-outdated", response_model=ReindexAllOut, status_code=status.HTTP_202_ACCEPTED
)
async def reindex_outdated(_: AdminDep, service: DocumentsDep) -> ReindexAllOut:
    """Reindexa solo los documentos desactualizados (p. ej. tras cambiar su título)."""
    return ReindexAllOut(scheduled=await service.reindex_outdated())


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, _: AdminDep, service: DocumentsDep) -> DocumentOut:
    return await _document_out(service, await _get_or_404(service, document_id))


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: str, body: DocumentUpdate, _: AdminDep, service: DocumentsDep
) -> DocumentOut:
    """Cambia el título o la categoría. No reindexa: el documento queda desactualizado."""
    changes = body.model_dump(include=body.model_fields_set)
    if "title" in changes:
        title = " ".join((changes["title"] or "").split())
        if not title:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "El título no puede estar vacío"
            )
        changes["title"] = title
    if "category_id" in changes:
        await _check_category(service, changes["category_id"])
    document = await service.update(document_id, changes)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return await _document_out(service, document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, _: AdminDep, service: DocumentsDep) -> None:
    if not await service.delete(document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")


@router.post(
    "/{document_id}/reindex", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED
)
async def reindex_document(document_id: str, _: AdminDep, service: DocumentsDep) -> DocumentOut:
    document = await service.reindex(document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return await _document_out(service, document)


@router.post("/{document_id}/refresh", response_model=DocumentOut)
async def refresh_document(document_id: str, _: AdminDep, service: DocumentsDep) -> DocumentOut:
    """Comprueba ahora si ha cambiado un documento web (#79) y lo devuelve actualizado (si
    ha cambiado, queda pendiente de reindexar). 404 si no existe o se ha borrado al no
    encontrarse la página."""
    try:
        document = await service.check_now(document_id)
    except UploadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return await _document_out(service, document)


@router.get("/{document_id}/file", response_class=FileResponse)
async def download_document(document_id: str, _: AdminDep, service: DocumentsDep) -> FileResponse:
    """Descarga el original (siempre como adjunto: nunca se renderiza el HTML subido). De
    las páginas web, su contenido limpio en Markdown."""
    document = await _get_or_404(service, document_id)
    path = service.file_path(document)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El fichero original no está disponible")
    filename = document.filename
    if document.kind == DocumentKind.WEB:
        filename = f"{PurePosixPath(filename).stem}.md"
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


# --- Acceso público a los originales (enlaces de las referencias del chat) ---

public_router = APIRouter(prefix="/documents", tags=["documents"])

# El HTML subido se muestra aislado: sin scripts, sin acceso al origen de la app
# y sin cargar recursos externos.
HTML_SANDBOX_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"

_INLINE_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "markdown": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    # Contenido limpio de una página web (las referencias enlazan a la página).
    "web": "text/plain; charset=utf-8",
}
# Office y OpenDocument: el navegador no los muestra, se descargan.
_DOWNLOAD_TYPES: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "odt": "application/vnd.oasis.opendocument.text",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
}


@public_router.get("", response_model=list[PublicDocumentOut])
async def list_indexed_documents(db: DbSession, _: CurrentUserDep) -> list[PublicDocumentOut]:
    """Documentos indexados, por título: para limitar la búsqueda del chat a algunos (#58)."""
    rows = await db.execute(
        select(
            Document.id,
            Document.filename,
            Document.title,
            Document.kind,
            Category.name,
            Document.category_id,
        )
        .outerjoin(Category, Document.category_id == Category.id)
        .where(Document.status == DocumentStatus.READY.value)
    )
    documents = [
        PublicDocumentOut(
            id=id,
            filename=filename,
            title=title,
            kind=kind,
            category=category,
            category_id=category_id,
        )
        for id, filename, title, kind, category, category_id in rows.all()
    ]
    return sorted(documents, key=lambda d: d.title.casefold())


@public_router.get("/{document_id}/file", response_class=FileResponse)
async def view_document(document_id: str, service: DocumentsDep, _: CurrentUserDep) -> FileResponse:
    """Muestra el original en el navegador (los PDF admiten `#page=N`); los de Office y
    OpenDocument se descargan."""
    document = await _get_or_404(service, document_id)
    path = service.file_path(document)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El fichero original no está disponible")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
    if document.kind == "html":
        headers["Content-Security-Policy"] = HTML_SANDBOX_CSP
    inline = document.kind in _INLINE_TYPES
    return FileResponse(
        path,
        filename=document.filename,
        media_type=_INLINE_TYPES.get(document.kind) or _DOWNLOAD_TYPES[document.kind],
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )
