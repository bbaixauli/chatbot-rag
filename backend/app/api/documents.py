"""Endpoints de administración de documentos del RAG."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.core.auth import AdminDep, CurrentUserDep
from app.db.models import Document
from app.services.documents import DocumentService, UploadError

router = APIRouter(prefix="/admin/documents", tags=["documents"])


def get_documents(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.documents
    return service


DocumentsDep = Annotated[DocumentService, Depends(get_documents)]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    kind: Literal["pdf", "markdown", "html"]
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


class UploadResultOut(BaseModel):
    filename: str
    document: DocumentOut | None = None
    duplicate: bool = False
    error: str | None = None


class StatsOut(BaseModel):
    documents: int
    chunks: int
    by_status: dict[str, int]
    embedding_model: str
    # Indexados con otro modelo de embeddings u otro troceado: hay que reindexarlos.
    outdated: int


class ReindexAllOut(BaseModel):
    scheduled: int


async def _get_or_404(service: DocumentService, document_id: str) -> Document:
    document = await service.get(document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return document


@router.get("", response_model=list[DocumentOut])
async def list_documents(_: AdminDep, service: DocumentsDep) -> list[Document]:
    return await service.list()


@router.post("", response_model=list[UploadResultOut])
async def upload_documents(
    files: list[UploadFile], admin: AdminDep, service: DocumentsDep
) -> list[UploadResultOut]:
    """Sube uno o varios ficheros. Cada uno se valida y procesa por separado."""
    results = []
    for file in files:
        filename = file.filename or "documento"
        try:
            result = await service.upload(filename, file, admin.username)
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


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, _: AdminDep, service: DocumentsDep) -> Document:
    return await _get_or_404(service, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, _: AdminDep, service: DocumentsDep) -> None:
    if not await service.delete(document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")


@router.post(
    "/{document_id}/reindex", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED
)
async def reindex_document(document_id: str, _: AdminDep, service: DocumentsDep) -> Document:
    document = await service.reindex(document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    return document


@router.get("/{document_id}/file", response_class=FileResponse)
async def download_document(document_id: str, _: AdminDep, service: DocumentsDep) -> FileResponse:
    """Descarga el original (siempre como adjunto: nunca se renderiza el HTML subido)."""
    document = await _get_or_404(service, document_id)
    path = service.file_path(document)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El fichero original no está disponible")
    return FileResponse(
        path,
        filename=document.filename,
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
}


@public_router.get("/{document_id}/file", response_class=FileResponse)
async def view_document(document_id: str, service: DocumentsDep, _: CurrentUserDep) -> FileResponse:
    """Muestra el original en el navegador (los PDF admiten `#page=N`)."""
    document = await _get_or_404(service, document_id)
    path = service.file_path(document)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El fichero original no está disponible")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
    if document.kind == "html":
        headers["Content-Security-Policy"] = HTML_SANDBOX_CSP
    return FileResponse(
        path,
        filename=document.filename,
        media_type=_INLINE_TYPES[document.kind],
        content_disposition_type="inline",
        headers=headers,
    )
