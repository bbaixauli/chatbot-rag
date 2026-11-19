import asyncio
from collections.abc import AsyncIterator

import pytest
from qdrant_client import AsyncQdrantClient

from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.qdrant_store import QdrantVectorStore, point_id
from app.services.vectorstore import VectorStoreError


@pytest.fixture
async def client() -> AsyncIterator[AsyncQdrantClient]:
    qdrant = AsyncQdrantClient(location=":memory:")
    yield qdrant
    await qdrant.close()


@pytest.fixture
def store(client: AsyncQdrantClient) -> QdrantVectorStore:
    return QdrantVectorStore(client, "pruebas")


CHUNKS = [
    Chunk(index=0, text="Horario de oficina de 8 a 15", page=1, heading=None),
    Chunk(index=1, text="Vacaciones: 23 días laborables", page=None, heading="Vacaciones"),
    Chunk(index=2, text="Gastos de viaje y dietas", page=2, heading="Gastos"),
]


async def _add(
    store: QdrantVectorStore, document_id: str, chunks: list[Chunk], dims: int = 32
) -> FakeEmbeddings:
    embeddings = FakeEmbeddings(dimensions=dims)
    vectors = await embeddings.embed([c.text for c in chunks])
    await store.add(document_id, f"{document_id}.md", chunks, vectors)
    return embeddings


async def test_empty_store(store: QdrantVectorStore) -> None:
    assert await store.count() == 0
    assert await store.search([0.1] * 32, 5) == []
    # Borrar sin colección no falla.
    await store.delete_document("x")
    await store.delete_chunks_from("x", 0)
    await store.add("x", "x.md", [], [])
    assert await store.count() == 0


async def test_add_search_and_metadata(store: QdrantVectorStore) -> None:
    embeddings = await _add(store, "d1", CHUNKS)

    [query] = await embeddings.embed(["Vacaciones: 23 días laborables"])
    hits = await store.search(query, 2)

    assert await store.count() == 3
    assert len(hits) == 2
    best = hits[0]
    assert (best.document_id, best.filename, best.chunk_index) == ("d1", "d1.md", 1)
    assert (best.page, best.heading) == (None, "Vacaciones")
    assert best.text == "Vacaciones: 23 días laborables"
    assert best.score == pytest.approx(1.0, abs=1e-4)
    assert hits[1].score < best.score


async def test_reindex_replaces_and_trims_chunks(store: QdrantVectorStore) -> None:
    await _add(store, "d1", CHUNKS)
    await _add(store, "d2", CHUNKS[:1])

    # Al reindexar con menos fragmentos se sustituyen y se eliminan los sobrantes.
    await _add(store, "d1", CHUNKS[:1])
    await store.delete_chunks_from("d1", 1)

    assert await store.count() == 2
    await store.delete_document("d1")
    assert await store.count() == 1
    hits = await store.search([0.1] * 32, 10)
    assert {h.document_id for h in hits} == {"d2"}


async def test_dimension_change_requires_reindexing_everything(
    client: AsyncQdrantClient, store: QdrantVectorStore
) -> None:
    await _add(store, "d1", CHUNKS, dims=32)
    # Otra instancia (p. ej. otro worker) reutiliza la colección existente.
    other = QdrantVectorStore(client, "pruebas")
    await _add(other, "d2", CHUNKS[:1], dims=32)
    assert await other.count() == 4

    # Cambio de modelo de embeddings: no se borra nada por su cuenta.
    with pytest.raises(VectorStoreError, match="Reindexar todo"):
        await _add(other, "d3", CHUNKS[:2], dims=16)
    assert await other.count() == 4

    # «Reindexar todo» vacía el índice y después se indexa con la nueva dimensión.
    await other.reset()
    await _add(other, "d3", CHUNKS[:2], dims=16)
    assert {h.document_id for h in await other.search([0.2] * 16, 5)} == {"d3"}


async def test_concurrent_workers_create_the_collection_once(client: AsyncQdrantClient) -> None:
    workers = [QdrantVectorStore(client, "concurrente") for _ in range(3)]

    await asyncio.gather(
        *(_add(worker, f"d{i}", CHUNKS, dims=32) for i, worker in enumerate(workers))
    )

    assert await workers[0].count() == 9


async def test_collection_creation_errors_propagate(
    client: AsyncQdrantClient, store: QdrantVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object, **__: object) -> bool:
        raise ConnectionError("Qdrant no responde")

    monkeypatch.setattr(client, "create_collection", broken)
    with pytest.raises(ConnectionError):
        await _add(store, "d1", CHUNKS)


async def test_reset(store: QdrantVectorStore) -> None:
    await _add(store, "d1", CHUNKS)
    await store.reset()
    assert await store.count() == 0
    await store.reset()  # sin colección
    await _add(store, "d1", CHUNKS[:1])
    assert await store.count() == 1


async def test_worker_recreates_a_collection_reset_by_another_process(
    client: AsyncQdrantClient,
) -> None:
    # La API vacía el índice («Reindexar todo») mientras los workers siguen en marcha:
    # un worker que ya había indexado no debe dar por hecho que la colección existe.
    worker = QdrantVectorStore(client, "compartida")
    api = QdrantVectorStore(client, "compartida")
    await _add(worker, "d1", CHUNKS)

    await api.reset()
    await _add(worker, "d1", CHUNKS)

    assert await api.count() == 3


def test_point_ids_are_stable_uuids() -> None:
    assert point_id("d1", 0) == point_id("d1", 0)
    assert point_id("d1", 0) != point_id("d1", 1)
    assert len(point_id("d1", 0)) == 36


async def test_from_url_does_not_connect_until_used() -> None:
    store = QdrantVectorStore.from_url("https://qdrant-inexistente:6333", "clave", "c")
    await store.close()


async def test_waits_while_another_worker_initialises_the_collection(
    client: AsyncQdrantClient, store: QdrantVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otro worker crea la colección a la vez y Qdrant tarda en tenerla lista."""
    from app.services import qdrant_store

    monkeypatch.setattr(qdrant_store, "COLLECTION_READY_DELAY_SECONDS", 0)
    real_create = client.create_collection
    real_get = client.get_collection
    pending_errors = [RuntimeError("0 of 0 read operations failed")] * 2

    async def create_then_conflict(*args: object, **kwargs: object) -> bool:
        await real_create(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("409: Collection already exists")

    async def get_while_initialising(*args: object, **kwargs: object) -> object:
        if pending_errors:
            raise pending_errors.pop()
        return await real_get(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(client, "create_collection", create_then_conflict)
    monkeypatch.setattr(client, "get_collection", get_while_initialising)

    await _add(store, "d1", CHUNKS)

    assert await store.count() == 3
    assert pending_errors == []


async def test_gives_up_if_the_collection_never_becomes_ready(
    client: AsyncQdrantClient, store: QdrantVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import qdrant_store

    monkeypatch.setattr(qdrant_store, "COLLECTION_READY_DELAY_SECONDS", 0)
    real_create = client.create_collection

    async def create_then_conflict(*args: object, **kwargs: object) -> bool:
        await real_create(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("409")

    async def always_failing(*_: object, **__: object) -> object:
        raise RuntimeError("no disponible")

    monkeypatch.setattr(client, "create_collection", create_then_conflict)
    monkeypatch.setattr(client, "get_collection", always_failing)

    with pytest.raises(RuntimeError, match="no disponible"):
        await _add(store, "d1", CHUNKS)
