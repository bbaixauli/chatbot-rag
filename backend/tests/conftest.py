"""Fixtures comunes de los tests del backend.

Con `CHATBOT_TEST_DATABASE_URL` (p. ej. `postgresql+asyncpg://chatbot:chatbot@localhost:55432/
chatbot_test`) la suite usa esa base de datos en lugar de un SQLite por test: se vacía antes de
cada test y los tests propios de SQLite (`@pytest.mark.sqlite_only`) se omiten. Los que
necesitan PostgreSQL (`@pytest.mark.postgres_only`) solo se ejecutan en ese caso.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

# Los tests no dependen de la máquina: no se lee `backend/.env` ni se heredan variables
# CHATBOT_* del entorno (un test que necesite una la define con `monkeypatch.setenv`).
# Se hace antes de importar `app.main`, que crea una aplicación al importarse.
TEST_DATABASE_URL = os.environ.get("CHATBOT_TEST_DATABASE_URL")
for _name in [name for name in os.environ if name.upper().startswith("CHATBOT_")]:
    del os.environ[_name]
Settings.model_config["env_file"] = None
if TEST_DATABASE_URL:
    # Todas las instancias de Settings de los tests usan la BD de pruebas.
    os.environ["CHATBOT_DATABASE_URL"] = TEST_DATABASE_URL

from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.llm import ChatMessage, LLMError, TokenUsage  # noqa: E402


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    marker, reason = (
        ("sqlite_only", "solo con SQLite")
        if TEST_DATABASE_URL
        else ("postgres_only", "necesita CHATBOT_TEST_DATABASE_URL (PostgreSQL)")
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if marker in item.keywords:
            item.add_marker(skip)


async def _empty_test_database(url: str) -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(autouse=True)
def _empty_database() -> None:
    """Con una BD de pruebas compartida, cada test empieza con ella vacía."""
    if TEST_DATABASE_URL:
        asyncio.run(_empty_test_database(TEST_DATABASE_URL))


class ScriptedLLM:
    """LLM de prueba: devuelve fragmentos fijos y registra los prompts recibidos."""

    def __init__(self, chunks: Sequence[str] = ("Hola", ", ", "mundo"), fail: bool = False):
        self.chunks = list(chunks)
        self.fail = fail
        self.error = LLMError("caído")
        self.calls: list[list[ChatMessage]] = []
        # Tokens que «cuenta el servidor» (#73), si se quiere simular.
        self.prompt_tokens: int | None = None

    async def stream_chat(
        self, messages: Sequence[ChatMessage], *, usage: TokenUsage | None = None
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        if usage is not None:
            usage.prompt_tokens = self.prompt_tokens
        for chunk in self.chunks:
            yield chunk
        if self.fail:
            raise self.error


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Convierte un cuerpo SSE en una lista de (evento, datos)."""
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def race_after_sha256_check(app: FastAPI, rival: Callable[[], Awaitable[object]]) -> None:
    """Simula dos operaciones simultáneas con el mismo contenido (#72): la primera vez que
    el servicio de documentos busca por `sha256`, ejecuta `rival` antes de devolver el
    resultado, de modo que la comprobación no ve lo que `rival` guarda."""
    service = app.state.documents
    original: async_sessionmaker[AsyncSession] = service._sessionmaker
    pending = True

    def make_session() -> AsyncSession:
        session = original()
        scalar = session.scalar

        async def racing_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal pending
            result = await scalar(statement, *args, **kwargs)
            # Solo la consulta que filtra por `sha256` (no las que lo devuelven).
            if pending and "documents.sha256 = :" in str(statement):
                pending = False
                await rival()
            return result

        session.scalar = racing_scalar  # type: ignore[method-assign]
        return session

    service._sessionmaker = make_session


async def set_config(app: FastAPI, group: str, **changes: Any) -> None:
    """Cambia la configuración vigente (como al guardarla en la administración)."""
    store = app.state.config
    current = store.current.group(group)
    await store.save(group, current.model_copy(update=changes), "test")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        llm_provider="fake",
        embedding_provider="fake",
    )


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
async def app(settings: Settings, llm: ScriptedLLM) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # Como en un arranque real, con la BD ya migrada: copia la configuración a la BD.
        await application.state.config.load(seed=True)
        application.state.llm = llm
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
