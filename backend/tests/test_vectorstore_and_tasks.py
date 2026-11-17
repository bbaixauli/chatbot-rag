import asyncio
import logging
from pathlib import Path

import pytest

from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.tasks import InProcessTaskRunner
from app.services.vectorstore import ChromaVectorStore


async def test_chroma_add_search_and_delete(tmp_path: Path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma")
    embeddings = FakeEmbeddings()
    chunks = [
        Chunk(0, "Vacaciones: 23 días al año", page=2, heading=None),
        Chunk(1, "Teletrabajo dos días por semana", page=None, heading="Teletrabajo"),
    ]
    assert await store.search((await embeddings.embed(["x"]))[0], 3) == []

    await store.add("d1", "manual.pdf", chunks, await embeddings.embed([c.text for c in chunks]))
    await store.add("d2", "otro.md", [], [])
    assert await store.count() == 2

    query = (await embeddings.embed(["¿cuántos días de vacaciones?"]))[0]
    hits = await store.search(query, limit=5)

    assert len(hits) == 2
    best = hits[0]
    assert (best.document_id, best.filename, best.chunk_index, best.page, best.heading) == (
        "d1",
        "manual.pdf",
        0,
        2,
        None,
    )
    assert best.score > hits[1].score
    assert hits[1].heading == "Teletrabajo"
    assert hits[1].page is None

    # Reindexar el mismo documento sustituye los fragmentos (upsert por id).
    await store.add("d1", "manual.pdf", chunks[:1], (await embeddings.embed([chunks[0].text])))
    assert await store.count() == 2

    await store.delete_document("d1")
    assert await store.count() == 0


async def test_task_runner_runs_logs_errors_and_drains(caplog: pytest.LogCaptureFixture) -> None:
    runner = InProcessTaskRunner(max_concurrency=1)
    done: list[int] = []

    async def ok(n: int) -> None:
        await asyncio.sleep(0)
        done.append(n)

    async def boom() -> None:
        raise RuntimeError("fallo")

    runner.submit("a", lambda: ok(1))
    runner.submit("b", boom)
    runner.submit("c", lambda: ok(2))
    with caplog.at_level(logging.ERROR):
        await runner.drain()

    assert done == [1, 2]
    assert "La tarea b ha fallado" in caplog.text


async def test_task_runner_cancel_all() -> None:
    runner = InProcessTaskRunner()
    started = asyncio.Event()

    async def slow() -> None:
        started.set()
        await asyncio.sleep(60)

    runner.submit("lenta", slow)
    await started.wait()
    await runner.cancel_all()
    await runner.drain()


async def test_delete_chunks_from_keeps_the_rest(tmp_path: Path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma")
    embeddings = FakeEmbeddings()
    chunks = [Chunk(i, f"texto {i}", page=None, heading=None) for i in range(4)]
    await store.add("d1", "a.md", chunks, await embeddings.embed([c.text for c in chunks]))
    await store.add("d2", "b.md", chunks[:1], await embeddings.embed(["texto 0"]))

    await store.delete_chunks_from("d1", 2)

    hits = await store.search((await embeddings.embed(["texto"]))[0], 10)
    assert sorted((h.document_id, h.chunk_index) for h in hits) == [("d1", 0), ("d1", 1), ("d2", 0)]


async def test_in_memory_store_can_be_dropped() -> None:
    store = ChromaVectorStore(None, collection="temporal")
    await store.add("d", "a.md", [Chunk(0, "x", None, None)], await FakeEmbeddings().embed(["x"]))
    assert await store.count() == 1
    store.drop()
