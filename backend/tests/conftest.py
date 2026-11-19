"""Fixtures comunes de los tests del backend."""

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.services.llm import ChatMessage, LLMError


class ScriptedLLM:
    """LLM de prueba: devuelve fragmentos fijos y registra los prompts recibidos."""

    def __init__(self, chunks: Sequence[str] = ("Hola", ", ", "mundo"), fail: bool = False):
        self.chunks = list(chunks)
        self.fail = fail
        self.error = LLMError("caído")
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.calls.append(list(messages))
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
        application.state.llm = llm
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
