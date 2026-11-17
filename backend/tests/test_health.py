from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_db


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["app"] == "Chatbot RAG"


async def test_health_degraded_when_database_fails(app: FastAPI, client: AsyncClient) -> None:
    broken_session = AsyncMock()
    broken_session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))

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
    }


async def test_data_dir_is_created(app: FastAPI) -> None:
    assert app.state.settings.data_dir.is_dir()
