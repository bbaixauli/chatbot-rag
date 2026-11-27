import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_db
from app.services.embeddings import EmbeddingError, FakeEmbeddings
from app.services.health import HealthChecker
from app.services.vectorstore import VectorStoreError


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["vector_store"] == "ok"
    assert body["embeddings"] == "ok"
    assert body["app"] == "Chatbot RAG"


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception("down")),
        # PostgreSQL parado: asyncpg no envuelve el error de la conexión.
        ConnectionRefusedError("conexión rechazada"),
    ],
)
async def test_health_degraded_when_database_fails(
    app: FastAPI, client: AsyncClient, error: Exception
) -> None:
    broken_session = AsyncMock()
    broken_session.execute.side_effect = error

    async def broken_db() -> AsyncIterator[AsyncMock]:
        yield broken_session

    app.dependency_overrides[get_db] = broken_db
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "app": "Chatbot RAG",
        "version": "0.1.0",
        "database": "error",
        "vector_store": "ok",
        "embeddings": "ok",
    }


async def test_data_dir_is_created(app: FastAPI) -> None:
    assert app.state.settings.data_dir.is_dir()


async def test_health_degraded_when_the_vector_store_fails(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> int:
        raise VectorStoreError("índice caído")

    monkeypatch.setattr(app.state.vector_store, "count", broken)
    body = (await client.get("/api/v1/health")).json()

    assert body["status"] == "degraded"
    assert (body["database"], body["vector_store"], body["embeddings"]) == ("ok", "error", "ok")


async def test_health_degraded_when_embeddings_fail(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("servicio caído")

    monkeypatch.setattr(app.state.embeddings, "embed", broken)
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert (body["vector_store"], body["embeddings"]) == ("ok", "error")


class CountingEmbeddings(FakeEmbeddings):
    def __init__(self, delay: float = 0, vector: list[float] | None = None) -> None:
        super().__init__()
        self.calls = 0
        self._delay = delay
        self._fixed = vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        await asyncio.sleep(self._delay)
        if self._fixed is not None:
            return [self._fixed for _ in texts]
        return await super().embed(texts)


class CountingStore:
    def __init__(self) -> None:
        self.calls = 0

    async def count(self) -> int:
        self.calls += 1
        return 3


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_checks_are_cached() -> None:
    store, embeddings, clock = CountingStore(), CountingEmbeddings(), Clock()
    checker = HealthChecker(store, embeddings, ttl=60, clock=clock)  # type: ignore[arg-type]

    first = await checker.documentation()
    clock.now += 59
    assert await checker.documentation() == first
    assert (store.calls, embeddings.calls) == (1, 1)

    clock.now += 1
    assert (await checker.documentation()).ok
    assert (store.calls, embeddings.calls) == (2, 2)


async def test_concurrent_requests_share_one_check() -> None:
    store, embeddings = CountingStore(), CountingEmbeddings(delay=0.05)
    checker = HealthChecker(store, embeddings)  # type: ignore[arg-type]

    results = await asyncio.gather(*(checker.documentation() for _ in range(5)))

    assert all(result.ok for result in results)
    assert (store.calls, embeddings.calls) == (1, 1)


@pytest.mark.parametrize(
    ("embeddings", "wait"),
    [
        # No responde a tiempo.
        (CountingEmbeddings(delay=1), 0.05),
        # Responde, pero con un vector vacío.
        (CountingEmbeddings(vector=[]), 5),
    ],
)
async def test_slow_or_empty_embeddings_are_errors(embeddings: Any, wait: float) -> None:
    checker = HealthChecker(CountingStore(), embeddings, timeout=wait)  # type: ignore[arg-type]

    result = await checker.documentation()

    assert (result.vector_store, result.embeddings) == ("ok", "error")
    assert not result.ok
