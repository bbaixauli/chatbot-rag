import asyncio
import logging
from pathlib import Path

import pytest

from app.services import vectorstore
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.tasks import InProcessTaskRunner
from app.services.vectorstore import (
    EXACT_SEARCH_MAX_CHUNKS,
    ChromaVectorStore,
    cosine_scores,
    exact_search,
)


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


async def test_chroma_reset_allows_a_new_embedding_dimension(tmp_path: Path) -> None:
    """Tras cambiar el modelo de embeddings, «Reindexar todo» vacía el índice primero."""
    store = ChromaVectorStore(tmp_path / "chroma")
    chunk = Chunk(index=0, text="texto", page=None, heading=None)
    await store.add("d1", "a.md", [chunk], [[0.1] * 32])

    with pytest.raises(Exception, match="dimension"):
        await store.add("d1", "a.md", [chunk], [[0.1] * 16])

    await store.reset()
    assert await store.count() == 0
    await store.add("d1", "a.md", [chunk], [[0.1] * 16])
    assert await store.count() == 1


async def test_chroma_document_chunks_returns_vectors_in_order() -> None:
    store = ChromaVectorStore(None, "fragmentos-documento")
    embeddings = FakeEmbeddings(dimensions=16)
    chunks = [
        Chunk(index=1, text="Vacaciones: 23 días", page=None, heading="Vacaciones"),
        Chunk(index=0, text="Horario de 8 a 15", page=1, heading=None),
    ]
    vectors = await embeddings.embed([c.text for c in chunks])
    await store.add("d1", "d1.md", chunks, vectors)
    await store.add("d2", "d2.md", chunks[:1], vectors[:1])

    stored = await store.document_chunks("d1")

    assert [c.chunk_index for c in stored] == [0, 1]
    assert (stored[0].text, stored[0].page, stored[0].heading) == ("Horario de 8 a 15", 1, None)
    assert stored[1].heading == "Vacaciones"
    assert stored[0].vector == pytest.approx(vectors[1], abs=1e-6)
    assert await store.document_chunks("otro") == []
    store.drop()


async def test_chroma_keeps_the_table_of_contents_mark() -> None:
    store = ChromaVectorStore(None, "marca-indice")
    chunks = [
        Chunk(index=0, text="Índice", page=1, heading=None, toc=True),
        Chunk(index=1, text="Contenido", page=2, heading=None),
    ]
    vectors = await FakeEmbeddings(dimensions=16).embed([c.text for c in chunks])
    await store.add("d1", "d1.pdf", chunks, vectors)

    hits = await store.search(vectors[0], 2)

    assert {h.text: h.toc for h in hits} == {"Índice": True, "Contenido": False}
    assert [c.toc for c in await store.document_chunks("d1")] == [True, False]

    # Al reindexar, un fragmento que deja de parecer un índice pierde la marca.
    await store.add("d1", "d1.pdf", [Chunk(0, "Ya no es índice", 1, None)], vectors[:1])
    assert [c.toc for c in await store.document_chunks("d1")] == [False, False]
    store.drop()


async def test_chroma_search_limited_to_documents_is_exact() -> None:
    store = ChromaVectorStore(None, "busqueda-limitada")
    embeddings = FakeEmbeddings(dimensions=16)
    texts = {"d1": ["Vacaciones anuales", "Permisos"], "d2": ["Dietas de viaje"], "d3": ["Otro"]}
    for document_id, chunk_texts in texts.items():
        chunks = [Chunk(i, text, 1, None) for i, text in enumerate(chunk_texts)]
        await store.add(
            document_id, f"{document_id}.md", chunks, await embeddings.embed(chunk_texts)
        )
    [query] = await embeddings.embed(["Vacaciones anuales"])

    hits = await store.search(query, 10, document_ids=["d1", "d2"])

    assert {h.document_id for h in hits} == {"d1", "d2"}
    assert hits[0].text == "Vacaciones anuales"
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert len(await store.search(query, 1, document_ids=["d1", "d2"])) == 1
    assert await store.search(query, 10, document_ids=[]) == []
    assert await store.search(query, 10, document_ids=["no-existe"]) == []
    store.drop()


WORDS = [
    "vacaciones",
    "días",
    "solicitud",
    "teletrabajo",
    "gastos",
    "dietas",
    "kilometraje",
    "horario",
    "oficina",
    "horas",
    "extra",
    "compensación",
    "permiso",
    "baja",
    "alta",
    "usuario",
    "cuenta",
    "acceso",
    "bloqueo",
    "intentos",
    "guía",
    "manual",
]


async def _filled_store(path: Path) -> tuple[ChromaVectorStore, list[str], list[list[float]]]:
    """Índice con varios documentos y fragmentos de textos variados (como los de los tests
    que fallaban de forma intermitente, #112)."""
    store = ChromaVectorStore(path)
    embeddings = FakeEmbeddings()
    texts: list[str] = []
    vectors: list[list[float]] = []
    for doc in range(6):
        chunk_texts = [
            " ".join(WORDS[(doc * 7 + i * 3 + k) % len(WORDS)] for k in range(3 + i % 5))
            for i in range(5)
        ]
        chunk_vectors = await embeddings.embed(chunk_texts)
        chunks = [Chunk(i, text, None, None) for i, text in enumerate(chunk_texts)]
        await store.add(f"d{doc}", f"d{doc}.md", chunks, chunk_vectors)
        texts += chunk_texts
        vectors += chunk_vectors
    return store, texts, vectors


def test_exact_search_thresholds() -> None:
    assert not exact_search(None)
    assert exact_search(None, EXACT_SEARCH_MAX_CHUNKS)
    assert not exact_search(None, EXACT_SEARCH_MAX_CHUNKS + 1)
    # Limitada a unos documentos, manda el número de documentos.
    assert exact_search(["a"], EXACT_SEARCH_MAX_CHUNKS + 1)


async def test_unlimited_search_in_a_small_index_is_exact(tmp_path: Path) -> None:
    store, _, vectors = await _filled_store(tmp_path / "chroma")
    total = await store.count()
    embeddings = FakeEmbeddings()

    for query_text in ("vacaciones días", "horario oficina horas", "Hola, mundo", "acceso"):
        [query] = await embeddings.embed([query_text])
        expected = sorted(cosine_scores(query, vectors), reverse=True)
        # Todo el índice (lo que pide `diagnose query --document`) y el top-k del chat.
        for limit in (total, 5):
            hits = await store.search(query, limit)
            assert len(hits) == limit
            assert [hit.score for hit in hits] == pytest.approx(expected[:limit], abs=1e-6)
    await store.close()


async def test_large_index_uses_the_approximate_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _ = await _filled_store(tmp_path / "chroma")
    monkeypatch.setattr(vectorstore, "EXACT_SEARCH_MAX_CHUNKS", 3)

    async def unexpected(*args: object) -> None:
        raise AssertionError("no debería compararse con todos los fragmentos")

    monkeypatch.setattr(store, "_search_documents", unexpected)
    [query] = await FakeEmbeddings().embed(["vacaciones días"])

    assert len(await store.search(query, 5)) == 5
    await store.close()


def test_cosine_scores() -> None:
    assert cosine_scores([1, 0], [[1, 0], [0, 1], [0, 0]]) == pytest.approx([1.0, 0.0, 0.0])
    assert cosine_scores([0, 0], [[1, 0]]) == [0.0]
    assert cosine_scores([1, 0], []) == []
