"""Cola de tareas con arq, worker de indexación y selección de backends."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app import backup, worker
from app.core.config import Settings
from app.db.models import Document, DocumentStatus
from app.services import backends
from app.services import documents as documents_service
from app.services.documents import DocumentService
from app.services.qdrant_store import QdrantVectorStore
from app.services.tasks import (
    INDEX_DOCUMENT,
    REFRESH_DOCUMENT,
    ArqTaskRunner,
    InProcessTaskRunner,
)
from app.services.vectorstore import ChromaVectorStore
from app.services.web import WebFetcher
from tests.conftest import set_config
from tests.factories import MARKDOWN
from tests.web_server import Server


class FakePool:
    """Sustituye a ArqRedis: registra los trabajos y simula la deduplicación por id."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], str]] = []
        self.closed = False

    async def enqueue_job(self, job: str, *args: Any, _job_id: str) -> object | None:
        if any(existing_id == _job_id for *_, existing_id in self.jobs):
            return None
        self.jobs.append((job, args, _job_id))
        return object()

    async def aclose(self) -> None:
        self.closed = True


async def test_arq_runner_enqueues_named_jobs_once(caplog: pytest.LogCaptureFixture) -> None:
    pool = FakePool()
    runner = ArqTaskRunner(pool)  # type: ignore[arg-type]
    runner.register(INDEX_DOCUMENT, lambda _: None)  # type: ignore[arg-type, return-value]

    with caplog.at_level(logging.INFO):
        await runner.enqueue(INDEX_DOCUMENT, "d1")
        await runner.enqueue(INDEX_DOCUMENT, "d1")
        await runner.enqueue(INDEX_DOCUMENT, "d2")
    await runner.drain()
    await runner.close()

    assert pool.jobs == [
        (INDEX_DOCUMENT, ("d1",), "index_document:d1"),
        (INDEX_DOCUMENT, ("d2",), "index_document:d2"),
    ]
    assert "ya estaba en la cola" in caplog.text
    assert pool.closed


async def test_arq_runner_connects_with_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[Any] = []

    async def fake_create_pool(settings: Any) -> FakePool:
        received.append(settings)
        return FakePool()

    monkeypatch.setattr("arq.create_pool", fake_create_pool)
    runner = await ArqTaskRunner.connect("redis://cola:6380/2")

    assert isinstance(runner, ArqTaskRunner)
    assert (received[0].host, received[0].port, received[0].database) == ("cola", 6380, 2)


async def test_in_process_runner_runs_registered_jobs() -> None:
    runner = InProcessTaskRunner()
    seen: list[str] = []

    async def handler(argument: str) -> None:
        seen.append(argument)

    runner.register("trabajo", handler)
    await runner.enqueue("trabajo", "a")
    await runner.enqueue("trabajo", "b")
    await runner.drain()
    await runner.close()

    assert sorted(seen) == ["a", "b"]


async def test_backends_follow_the_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    chroma = backends.build_vector_store(settings)
    assert isinstance(chroma, ChromaVectorStore)
    await backends.close_vector_store(chroma)  # sin close(): no hace nada
    assert isinstance(await backends.build_task_runner(settings), InProcessTaskRunner)

    from pydantic import SecretStr

    scaled = settings.model_copy(
        update={
            "vector_store": "qdrant",
            "task_runner": "arq",
            "qdrant_url": "https://qdrant.interno:6333",
            "qdrant_api_key": SecretStr("k"),
        }
    )
    qdrant = backends.build_vector_store(scaled)
    assert isinstance(qdrant, QdrantVectorStore)
    await backends.close_vector_store(qdrant)

    async def fake_connect(url: str) -> ArqTaskRunner:
        return ArqTaskRunner(FakePool())  # type: ignore[arg-type]

    monkeypatch.setattr(ArqTaskRunner, "connect", fake_connect)
    assert isinstance(await backends.build_task_runner(scaled), ArqTaskRunner)


def test_arq_requires_qdrant() -> None:
    with pytest.raises(ValidationError, match="CHATBOT_VECTOR_STORE=qdrant"):
        Settings(task_runner="arq")
    assert Settings(task_runner="arq", vector_store="qdrant").task_runner == "arq"


async def test_api_with_arq_enqueues_instead_of_indexing(app: FastAPI, settings: Settings) -> None:
    """La API solo encola: el documento queda pendiente hasta que lo procese un worker."""
    pool = FakePool()
    service = DocumentService(
        app.state.sessionmaker,
        settings,
        app.state.embeddings,
        app.state.vector_store,
        ArqTaskRunner(pool),  # type: ignore[arg-type]
        app.state.config,
    )

    from io import BytesIO

    class Upload:
        def __init__(self, data: bytes) -> None:
            self._data = BytesIO(data)

        async def read(self, size: int = -1) -> bytes:
            return self._data.read(size)

    result = await service.upload("guia.md", Upload(MARKDOWN.encode()), "admin")
    assert result.document is not None
    document_id = result.document.id

    [(job, args, job_id)] = pool.jobs
    assert (job, args) == (INDEX_DOCUMENT, (document_id,))
    assert job_id.startswith(f"index_document:{document_id}:")
    assert (await service.get(document_id)).status == DocumentStatus.PENDING.value  # type: ignore[union-attr]
    # Al reiniciar la API, los pendientes se vuelven a encolar sin duplicarse.
    assert await service.resume_pending() == 1
    assert len(pool.jobs) == 1


async def test_reindex_requested_while_a_job_is_running_is_not_lost(
    app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con arq, un trabajo en curso deduplica su id: una reindexación pedida mientras
    tanto (p. ej. «Reindexar todo», que vacía el índice) debe encolar otro trabajo.

    El reloj se congela: con poca resolución (Windows), dos peticiones seguidas pueden
    tener la misma hora y aun así deben ser trabajos distintos."""
    frozen = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(documents_service, "utcnow", lambda: frozen)
    pool = FakePool()
    service = DocumentService(
        app.state.sessionmaker,
        settings,
        app.state.embeddings,
        app.state.vector_store,
        ArqTaskRunner(pool),  # type: ignore[arg-type]
        app.state.config,
    )
    async with app.state.sessionmaker() as session:
        document = Document(
            filename="guia.md",
            title="guia.md",
            kind="markdown",
            size_bytes=len(MARKDOWN),
            sha256="1" * 64,
            uploaded_by="admin",
        )
        session.add(document)
        await session.commit()
        document_id = document.id

    # Primer trabajo: sigue en la cola (pendiente o en curso en un worker).
    await service.reindex(document_id)
    # Segunda petición mientras tanto: no puede descartarse por tener el mismo id.
    await service.reindex(document_id)

    ids = [job_id for *_, job_id in pool.jobs]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    # Al reiniciar la API se reconoce el último trabajo encolado y no se duplica.
    assert await service.resume_pending() == 1
    assert len(pool.jobs) == 2


async def test_worker_indexes_documents_into_qdrant(
    app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Documento subido por la API (con la cola de arq) y aún pendiente.
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    async with app.state.sessionmaker() as session:
        document = Document(
            filename="guia.md",
            title="guia.md",
            kind="markdown",
            size_bytes=len(MARKDOWN),
            sha256="0" * 64,
            uploaded_by="admin",
        )
        session.add(document)
        await session.commit()
        document_id = document.id
    (settings.uploads_dir / f"{document_id}.md").write_text(MARKDOWN, encoding="utf-8")

    qdrant = QdrantVectorStore(AsyncQdrantClient(location=":memory:"), "worker")
    monkeypatch.setattr(worker, "build_vector_store", lambda _: qdrant)
    scaled = settings.model_copy(update={"vector_store": "qdrant", "task_runner": "arq"})
    ctx: dict[str, Any] = {"settings": scaled, "redis": FakePool()}

    await worker.startup(ctx)
    # Cambio guardado en la administración después de arrancar el worker: lo aplica en el
    # siguiente trabajo.
    await set_config(app, "indexing", chunk_size_chars=250)
    await worker.index_document(ctx, document_id)
    async with app.state.sessionmaker() as session:
        indexed = await session.get(Document, document_id)
    count = await qdrant.count()
    await worker.shutdown(ctx)

    assert indexed is not None
    assert (indexed.status, indexed.chunk_count) == (DocumentStatus.READY.value, count)
    assert count > 0
    assert indexed.chunk_size_chars == 250


async def test_worker_refreshes_web_documents_and_queues_their_indexing(
    app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Documento web ya indexado; la página ha cambiado.
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    url = "https://intranet/gastos"
    async with app.state.sessionmaker() as session:
        document = Document(
            filename="gastos.html",
            title="Gastos",
            kind="web",
            size_bytes=4,
            sha256="0" * 64,
            status=DocumentStatus.READY.value,
            uploaded_by="admin",
            source_url=url,
        )
        session.add(document)
        await session.commit()
        document_id = document.id
    (settings.uploads_dir / f"{document_id}.md").write_text("# Antes", encoding="utf-8")
    server = Server()
    server.page(
        url,
        "<html><body><main><h1>Gastos</h1><p>Nueva dieta.</p></main></body></html>",
        "text/html",
    )

    qdrant = QdrantVectorStore(AsyncQdrantClient(location=":memory:"), "worker")
    monkeypatch.setattr(worker, "build_vector_store", lambda _: qdrant)
    scaled = settings.model_copy(update={"vector_store": "qdrant", "task_runner": "arq"})
    pool = FakePool()
    ctx: dict[str, Any] = {"settings": scaled, "redis": pool}
    await worker.startup(ctx)
    ctx["documents"].fetcher = WebFetcher(httpx.MockTransport(server.handle))

    await worker.refresh_document(ctx, document_id)
    await worker.shutdown(ctx)

    async with app.state.sessionmaker() as session:
        refreshed = await session.get(Document, document_id)
    assert refreshed is not None
    assert refreshed.status == DocumentStatus.PENDING.value
    # La indexación vuelve a la cola de Redis, para cualquier worker.
    assert [(job, args) for job, args, _ in pool.jobs] == [(INDEX_DOCUMENT, (document_id,))]


def test_worker_settings() -> None:
    assert [f.name for f in worker.WorkerSettings.functions] == [INDEX_DOCUMENT, REFRESH_DOCUMENT]
    assert worker.WorkerSettings.keep_result == 0
    assert worker.WorkerSettings.max_tries == 3


@pytest.mark.sqlite_only
async def test_sqlite_uses_wal_and_waits_for_locks(app: FastAPI) -> None:
    async with app.state.engine.connect() as connection:
        mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar()
        timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar()
    assert (mode, timeout) == ("wal", 10000)


async def test_restore_empties_qdrant(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant = QdrantVectorStore(AsyncQdrantClient(location=":memory:"), "copias")
    from app.services.ingestion.chunker import Chunk

    await qdrant.add("antiguo", "a.md", [Chunk(0, "texto", None, None)], [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(QdrantVectorStore, "from_url", lambda *_: qdrant)
    closed: list[bool] = []

    async def record_close() -> None:
        closed.append(True)

    monkeypatch.setattr(qdrant, "close", record_close)
    scaled = settings.model_copy(update={"vector_store": "qdrant"})

    await backup.reset_external_index(scaled)

    assert await qdrant.count() == 0
    assert closed == [True]
    await backup.reset_external_index(settings)  # con ChromaDB no hace nada
    assert closed == [True]


def test_restore_command_resets_qdrant(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(backup, "restore_backup", lambda *_: 2)

    async def fake_reset(current: Settings) -> None:
        calls.append(current.vector_store)

    monkeypatch.setattr(backup, "reset_external_index", fake_reset)
    scaled = settings.model_copy(update={"vector_store": "qdrant"})

    assert backup.main(["restore", "copia.tar.gz"], scaled) == 0
    assert backup.main(["restore", "copia.tar.gz"], settings) == 0
    assert calls == ["qdrant"]


async def test_dimension_mismatch_is_reported_to_the_admin(
    app: FastAPI, settings: Settings
) -> None:
    from app.services.ingestion.chunker import Chunk

    qdrant = QdrantVectorStore(AsyncQdrantClient(location=":memory:"), "mezcla")
    # Índice creado con otro modelo (32 dimensiones; FakeEmbeddings genera 256).
    await qdrant.add("antiguo", "a.md", [Chunk(0, "texto", None, None)], [[0.1] * 32])
    service = DocumentService(
        app.state.sessionmaker,
        settings,
        app.state.embeddings,
        qdrant,
        InProcessTaskRunner(),
        app.state.config,
    )
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    async with app.state.sessionmaker() as session:
        document = Document(
            filename="guia.md",
            title="guia.md",
            kind="markdown",
            size_bytes=len(MARKDOWN),
            sha256="1" * 64,
            uploaded_by="admin",
        )
        session.add(document)
        await session.commit()
    (settings.uploads_dir / f"{document.id}.md").write_text(MARKDOWN, encoding="utf-8")

    await service.process(document.id)

    failed = await service.get(document.id)
    assert failed is not None
    assert failed.status == DocumentStatus.ERROR.value
    assert "Reindexar todo" in (failed.error or "")
