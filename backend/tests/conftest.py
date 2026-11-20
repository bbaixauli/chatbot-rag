"""Fixtures comunes de los tests del backend."""

import json
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings

# Los tests no dependen de la máquina: no se lee `backend/.env` ni se heredan variables
# CHATBOT_* del entorno (un test que necesite una la define con `monkeypatch.setenv`).
# Se hace antes de importar `app.main`, que crea una aplicación al importarse.
for _name in [name for name in os.environ if name.upper().startswith("CHATBOT_")]:
    del os.environ[_name]
Settings.model_config["env_file"] = None

from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.llm import ChatMessage, LLMError  # noqa: E402


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
