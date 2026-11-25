from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.db.models import Document, DocumentStatus
from app.main import create_app
from app.services.documents import safe_filename
from app.services.embeddings import EmbeddingError
from tests.conftest import set_config
from tests.factories import HTML, MARKDOWN, pdf_bytes

URL = "/api/v1/admin/documents"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    settings.chunk_size_chars = 200
    settings.chunk_overlap_chars = 20
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


async def upload(client: AsyncClient, *files: tuple[str, bytes]) -> list[dict[str, Any]]:
    response = await client.post(URL, files=[("files", (name, data)) for name, data in files])
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()
    return results


async def drain(app: FastAPI) -> None:
    await app.state.tasks.drain()


async def test_requires_admin(client: AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.post(URL, files=[("files", ("a.md", b"x"))])).status_code == 401
    assert (await client.get(f"{URL}/stats")).status_code == 401


async def test_upload_and_index_all_formats(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    results = await upload(
        admin,
        ("guia.md", MARKDOWN.encode()),
        ("gastos.html", HTML.encode()),
        ("manual.pdf", pdf_bytes(tmp_path, ["Pagina uno", "Pagina dos"])),
    )
    assert [(r["filename"], r["duplicate"], r["error"]) for r in results] == [
        ("guia.md", False, None),
        ("gastos.html", False, None),
        ("manual.pdf", False, None),
    ]
    assert {r["document"]["status"] for r in results} <= {"pending", "processing", "ready"}

    await drain(app)

    documents = {d["filename"]: d for d in (await admin.get(URL)).json()}
    assert {d["status"] for d in documents.values()} == {"ready"}
    assert documents["guia.md"]["kind"] == "markdown"
    assert documents["gastos.html"]["kind"] == "html"
    assert documents["manual.pdf"]["chunk_count"] == 2
    assert documents["manual.pdf"]["embedding_model"] == "fake-hashing"
    assert documents["guia.md"]["uploaded_by"] == "admin"

    stats = (await admin.get(f"{URL}/stats")).json()
    total_chunks = sum(d["chunk_count"] for d in documents.values())
    assert stats == {
        "documents": 3,
        "chunks": total_chunks,
        "by_status": {"ready": 3},
        "embedding_model": "fake-hashing",
        "outdated": 0,
    }
    assert await app.state.vector_store.count() == total_chunks

    hits = await app.state.vector_store.search(
        (await app.state.embeddings.embed(["días de vacaciones"]))[0], 1
    )
    assert hits[0].filename == "guia.md"
    assert hits[0].heading == "Guía de vacaciones"


async def test_duplicates_and_rejections(app: FastAPI, admin: AsyncClient) -> None:
    await set_config(app, "indexing", upload_max_mb=1)
    first = await upload(admin, ("a.md", b"# Hola"))
    results = await upload(
        admin,
        ("copia.md", b"# Hola"),
        ("hoja.xls", b"x"),
        ("vacio.md", b""),
        ("grande.md", b"a" * (1024 * 1024 + 1)),
    )

    assert results[0]["duplicate"] is True
    assert results[0]["document"]["id"] == first[0]["document"]["id"]
    assert "no admitido" in results[1]["error"]
    assert "vacío" in results[2]["error"]
    assert "tamaño máximo (1 MB)" in results[3]["error"]
    await drain(app)
    assert len((await admin.get(URL)).json()) == 1
    leftovers = [p.name for p in app.state.settings.uploads_dir.iterdir()]
    assert len(leftovers) == 1  # solo el original; ningún temporal


async def test_parse_errors_mark_document_as_error(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    results = await upload(
        admin,
        ("escaneado.pdf", pdf_bytes(tmp_path, [""])),
        ("roto.pdf", b"no es un pdf"),
    )
    await drain(app)

    for result in results:
        document = (await admin.get(f"{URL}/{result['document']['id']}")).json()
        assert document["status"] == "error"
        assert document["chunk_count"] == 0
        assert document["embedding_model"] is None
    assert "OCR" in (await admin.get(f"{URL}/{results[0]['document']['id']}")).json()["error"]
    stats = (await admin.get(f"{URL}/stats")).json()
    assert stats["by_status"] == {"error": 2}
    assert stats["chunks"] == 0


class FailingEmbeddings:
    model_name = "roto"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise self.error


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (EmbeddingError("servicio caído"), "servicio caído"),
        (RuntimeError("detalle interno"), "Error inesperado al procesar el documento"),
    ],
)
async def test_embedding_errors_mark_document_as_error(
    app: FastAPI, admin: AsyncClient, error: Exception, message: str
) -> None:
    app.state.documents._embeddings = FailingEmbeddings(error)

    [result] = await upload(admin, ("a.md", b"# Hola\n\ntexto"))
    await drain(app)

    document = (await admin.get(f"{URL}/{result['document']['id']}")).json()
    assert document["status"] == "error"
    assert document["error"] == message


async def test_delete_removes_file_vectors_and_row(app: FastAPI, admin: AsyncClient) -> None:
    [result] = await upload(admin, ("a.md", MARKDOWN.encode()))
    await drain(app)
    document_id = result["document"]["id"]
    assert await app.state.vector_store.count() > 0

    assert (await admin.delete(f"{URL}/{document_id}")).status_code == 204

    assert await app.state.vector_store.count() == 0
    assert list(app.state.settings.uploads_dir.iterdir()) == []
    assert (await admin.get(f"{URL}/{document_id}")).status_code == 404
    assert (await admin.delete(f"{URL}/{document_id}")).status_code == 404


async def test_reindex_one_and_all(app: FastAPI, admin: AsyncClient) -> None:
    paragraphs = [f"Norma {i}: " + "texto " * 8 for i in range(12)]
    long_text = "# Normas\n\n" + "\n\n".join(paragraphs)
    [result] = await upload(admin, ("a.md", long_text.encode()))
    await drain(app)
    document_id = result["document"]["id"]
    chunks = await app.state.vector_store.count()

    # Con fragmentos más pequeños hay más; al volver a agrandarlos sobran y se eliminan.
    await set_config(app, "indexing", chunk_size_chars=120)
    response = await admin.post(f"{URL}/{document_id}/reindex")
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    await drain(app)
    reindexed = (await admin.get(f"{URL}/{document_id}")).json()
    assert reindexed["status"] == "ready"
    assert reindexed["chunk_count"] > chunks
    assert await app.state.vector_store.count() == reindexed["chunk_count"]
    await set_config(app, "indexing", chunk_size_chars=2000)
    await admin.post(f"{URL}/{document_id}/reindex")
    await drain(app)
    shrunk = (await admin.get(f"{URL}/{document_id}")).json()["chunk_count"]
    assert shrunk < chunks
    assert await app.state.vector_store.count() == shrunk

    assert (await admin.post(f"{URL}/reindex")).json() == {"scheduled": 1}
    await drain(app)
    assert (await admin.post(f"{URL}/nope/reindex")).status_code == 404


async def test_reindex_all_after_changing_the_embedding_model(
    app: FastAPI, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con otro modelo cambia la dimensión de los vectores: el índice se vacía antes."""
    from app.services.embeddings import FakeEmbeddings

    await upload(admin, ("guia.md", MARKDOWN.encode()), ("gastos.html", HTML.encode()))
    await drain(app)
    # Un fragmento de un documento que ya no está en la BD no debe sobrevivir.
    from app.services.ingestion.chunker import Chunk

    [vector] = await app.state.embeddings.embed(["texto huérfano"])
    await app.state.vector_store.add("huerfano", "x.md", [Chunk(0, "texto", None, None)], [vector])

    monkeypatch.setattr(app.state.documents, "_embeddings", FakeEmbeddings(dimensions=64))
    assert (await admin.post(f"{URL}/reindex")).json() == {"scheduled": 2}
    await drain(app)

    documents = (await admin.get(URL)).json()
    assert {d["status"] for d in documents} == {"ready"}
    assert await app.state.vector_store.count() == sum(d["chunk_count"] for d in documents)


async def test_download_is_always_an_attachment(app: FastAPI, admin: AsyncClient) -> None:
    [result] = await upload(admin, ("página.html", HTML.encode()))
    document_id = result["document"]["id"]

    response = await admin.get(f"{URL}/{document_id}/file")

    assert response.status_code == 200
    assert response.content == HTML.encode()
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (await admin.get(f"{URL}/nope/file")).status_code == 404

    await drain(app)
    path = app.state.documents.file_path(await app.state.documents.get(document_id))
    path.unlink()
    assert (await admin.get(f"{URL}/{document_id}/file")).status_code == 404


async def test_document_deleted_while_processing(app: FastAPI, admin: AsyncClient) -> None:
    [result] = await upload(admin, ("a.md", MARKDOWN.encode()))
    document_id = result["document"]["id"]
    async with app.state.sessionmaker() as session:
        await session.delete(await session.get(Document, document_id))
        await session.commit()

    await drain(app)
    await app.state.documents.process(document_id)  # no existe: no hace nada

    assert await app.state.vector_store.count() == 0


async def test_pending_documents_resume_on_startup(settings: Settings, app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        session.add(
            Document(
                id="d1",
                filename="a.md",
                title="a.md",
                kind="markdown",
                size_bytes=6,
                sha256="0" * 64,
                status=DocumentStatus.PROCESSING.value,
                uploaded_by="admin",
            )
        )
        await session.commit()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / "d1.md").write_text("# Hola", encoding="utf-8")

    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        await restarted.state.tasks.drain()
        document = await restarted.state.documents.get("d1")

    assert document.status == "ready"


async def test_startup_without_tables_does_not_crash(tmp_path: Path) -> None:
    fresh = create_app(Settings(data_dir=tmp_path / "vacio", embedding_provider="fake"))
    async with fresh.router.lifespan_context(fresh):
        assert fresh.state.documents is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("informe.pdf", "informe.pdf"),
        ("C:\\Users\\x\\informe.pdf", "informe.pdf"),
        ("../../etc/passwd.md", "passwd.md"),
        ("ma\x00l\nicioso.md", "malicioso.md"),
        ("", "documento"),
        ("a" * 300 + ".md", "a" * 255),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected
