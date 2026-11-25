"""Documento completo cuando la búsqueda se limita a un solo documento (#58, fase 5)."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.core.runtime_config import StaticConfig
from app.db.base import Base
from app.diagnose import main
from app.main import create_app
from app.services.chat import select_sources
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.rag import (
    FULL_DOCUMENT_NOTE,
    ContextSource,
    Retriever,
    build_system_prompt,
    full_document_sources,
)
from app.services.vectorstore import ChromaVectorStore
from tests.conftest import ScriptedLLM, parse_sse, set_config
from tests.factories import MARKDOWN

# Página 1 en dos fragmentos que se solapan; página 2 en uno.
CHUNKS = [
    Chunk(0, "Objeto de la norma. Se aplica a todo el personal de DTIC.", 1, None),
    Chunk(1, "Se aplica a todo el personal de DTIC. Las cuentas se revisan.", 1, None),
    Chunk(2, "Las altas se piden al CAU y las bajas se tramitan en 24 horas.", 2, None),
]


@pytest.fixture
async def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(tmp_path / "chroma")
    texts = [c.text for c in CHUNKS]
    await store.add("d1", "NOR-013.pdf", CHUNKS, await FakeEmbeddings().embed(texts))
    return store


async def test_full_document_is_rebuilt_by_page(store: ChromaVectorStore) -> None:
    parts = await full_document_sources(store, "d1", 1000)

    assert parts is not None
    assert [(p.number, p.page, p.label) for p in parts] == [
        (1, 1, "NOR-013.pdf, pág. 1"),
        (2, 2, "NOR-013.pdf, pág. 2"),
    ]
    assert parts[0].text == (
        "Objeto de la norma. Se aplica a todo el personal de DTIC.\n\nLas cuentas se revisan."
    )
    assert all(p.full_document for p in parts)
    # Demasiado largo o inexistente: no se envía completo.
    assert await full_document_sources(store, "d1", 50) is None
    assert await full_document_sources(store, "otro", 1000) is None


async def test_full_document_by_section(tmp_path: Path) -> None:
    store = ChromaVectorStore(None, "por-apartado")
    chunks = [
        Chunk(0, "Texto A1", None, "Alcance"),
        Chunk(1, "Texto A2", None, "Alcance"),
        Chunk(2, "Texto B", None, "Altas"),
    ]
    await store.add("d1", "norma.md", chunks, await FakeEmbeddings().embed(["a", "b", "c"]))

    parts = await full_document_sources(store, "d1", 1000)

    assert parts is not None
    assert [(p.label, p.text) for p in parts] == [
        ("norma.md — Alcance", "Texto A1\n\nTexto A2"),
        ("norma.md — Altas", "Texto B"),
    ]
    store.drop()


def _retriever(store: Any, **overrides: Any) -> Retriever:
    settings = Settings(**{"rag_full_document_max_chars": 1000, **overrides})
    return Retriever(FakeEmbeddings(), store, StaticConfig.from_settings(settings))


async def test_retriever_sends_the_whole_document_only_when_it_applies(
    store: ChromaVectorStore,
) -> None:
    full = await _retriever(store).retrieve("resúmeme el NOR-013", ["d1"])
    assert [s.full_document for s in full] == [True, True]

    # Sin limitar, con varios documentos, desactivado o si no cabe: fragmentos.
    for sources in [
        await _retriever(store, rag_min_score=0).retrieve("altas CAU"),
        await _retriever(store).retrieve("altas CAU", ["d1", "d2"]),
        await _retriever(store, rag_full_document_max_chars=0).retrieve("altas CAU", ["d1"]),
        await _retriever(store, rag_full_document_max_chars=50).retrieve("altas CAU", ["d1"]),
    ]:
        assert sources
        assert not any(s.full_document for s in sources)


async def test_retriever_falls_back_if_the_document_cannot_be_read(
    store: ChromaVectorStore,
) -> None:
    class Broken:
        def __init__(self, inner: ChromaVectorStore) -> None:
            self._inner = inner

        async def search(self, *args: Any) -> Any:
            return await self._inner.search(*args)

        async def document_chunks(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("caído")

    sources = await _retriever(Broken(store)).retrieve("altas CAU", ["d1"])

    assert sources
    assert not any(s.full_document for s in sources)


def _part(number: int, page: int) -> ContextSource:
    return ContextSource(number, "d1", "NOR-013.pdf", page, None, 0, 1.0, "…", full_document=True)


def test_prompt_and_sources_of_a_whole_document() -> None:
    parts = [_part(1, 1), _part(2, 2), _part(3, 3)]

    assert FULL_DOCUMENT_NOTE in build_system_prompt("Base", parts)
    assert FULL_DOCUMENT_NOTE not in build_system_prompt(
        "Base", [ContextSource(1, "d1", "a.pdf", 1, None, 0, 0.5, "…")]
    )
    # Citadas: solo esas partes.
    assert [(s.number, cited) for s, cited in select_sources(parts, "Según [2].")] == [(2, True)]
    # Sin citas: el documento una sola vez.
    [(only, cited)] = select_sources(parts, "Resumen sin citas.")
    assert (only.label, only.page, cited) == ("NOR-013.pdf", None, False)


async def test_document_chunks_without_vectors(store: ChromaVectorStore) -> None:
    chunks = await store.document_chunks("d1", with_vectors=False)

    assert [(c.chunk_index, c.vector, c.filename) for c in chunks] == [
        (0, [], "NOR-013.pdf"),
        (1, [], "NOR-013.pdf"),
        (2, [], "NOR-013.pdf"),
    ]


# --- En el chat y en el diagnóstico ----------------------------------------------------------

NORMA = """# Control de acceso

Las cuentas de usuario se bloquean tras 5 intentos fallidos.

# Altas y bajas

Las altas se piden al CAU. Las bajas se tramitan en 24 horas."""


class Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


async def test_chat_sends_the_whole_named_document(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    result = await app.state.documents.upload("NOR-013 Control.md", Bytes(NORMA.encode()), "a")
    await app.state.documents.upload("guia.md", Bytes(MARKDOWN.encode()), "admin")
    await app.state.tasks.drain()
    await set_config(app, "rag", full_document_max_chars=5000)
    llm.chunks = ["Resumen: bloqueos [1] y altas [2]."]

    response = await client.post("/api/v1/chat", json={"message": "Resúmeme el NOR-013"})

    system = llm.calls[-1][0].content
    assert FULL_DOCUMENT_NOTE.strip() in system
    assert "[1] Fuente: NOR-013 Control.md — Control de acceso" in system
    assert "[2] Fuente: NOR-013 Control.md — Altas y bajas" in system
    assert "guia.md" not in system
    events = parse_sse(response.text)
    sources = next(data["sources"] for kind, data in events if kind == "sources")
    assert [(s["number"], s["document_id"], s["cited"]) for s in sources] == [
        (1, result.document.id, True),
        (2, result.document.id, True),
    ]


async def _prepare(settings: Settings) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await application.state.documents.upload("NOR-013 Control.md", Bytes(NORMA.encode()), "a")
        await application.state.tasks.drain()


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (5000, "El chat enviaría el documento completo: 2 partes"),
        (20, "supera el límite de 20 caracteres"),
    ],
)
def test_diagnose_reports_the_whole_document(
    settings: Settings, capsys: pytest.CaptureFixture[str], limit: int, expected: str
) -> None:
    settings.rag_full_document_max_chars = limit
    asyncio.run(_prepare(settings))

    assert main(["query", "Resúmeme el NOR-013"], settings) == 0
    assert expected in capsys.readouterr().out
