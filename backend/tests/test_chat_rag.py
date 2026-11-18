"""Chat con documentación: fuentes en el streaming, persistencia y enlaces."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.services.llm import ChatMessage, FakeLLMClient
from tests.conftest import ScriptedLLM, parse_sse
from tests.factories import HTML, MARKDOWN, pdf_bytes


class Bytes:
    """Lector asíncrono mínimo para DocumentService.upload."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.rag_min_score = 0.2
    settings.chunk_size_chars = 300
    return settings


async def index(app: FastAPI, filename: str, data: bytes) -> str:
    result = await app.state.documents.upload(filename, Bytes(data), "admin")
    await app.state.tasks.drain()
    document_id: str = result.document.id
    return document_id


async def ask(client: AsyncClient, message: str) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post("/api/v1/chat", json={"message": message})
    assert response.status_code == 200
    return parse_sse(response.text)


def sources_of(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return next((data["sources"] for kind, data in events if kind == "sources"), [])


async def test_without_documents_there_are_no_sources(
    client: AsyncClient, llm: ScriptedLLM
) -> None:
    events = await ask(client, "¿Cuántos días de vacaciones tengo?")

    assert "sources" not in [kind for kind, _ in events]
    assert "Documentación de referencia" not in llm.calls[0][0].content


async def test_cited_sources_are_streamed_saved_and_listed(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    document_id = await index(app, "guia.md", MARKDOWN.encode())
    llm.chunks = ["Tienes 23 días ", "de vacaciones [1]."]

    events = await ask(client, "¿Cuántos días de vacaciones tengo al año?")

    system = llm.calls[0][0].content
    assert "## Documentación de referencia" in system
    assert "[1] Fuente: guia.md — Guía de vacaciones" in system
    kinds = [kind for kind, _ in events]
    assert kinds[-2:] == ["sources", "done"]
    [source] = sources_of(events)
    assert source == {
        "number": 1,
        "document_id": document_id,
        "filename": "guia.md",
        "page": None,
        "heading": "Guía de vacaciones",
        "snippet": source["snippet"],
        "cited": True,
    }
    assert "23 días laborables" in source["snippet"]

    conversation_id = events[0][1]["id"]
    messages = (await client.get(f"/api/v1/conversations/{conversation_id}/messages")).json()
    assert messages[0]["sources"] == []
    assert messages[1]["sources"] == [source]


async def test_uncited_answer_shows_consulted_sources(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await index(app, "guia.md", MARKDOWN.encode())
    llm.chunks = ["Respuesta sin citas."]

    sources = sources_of(await ask(client, "vacaciones días solicitud portal empleado"))

    assert len(sources) >= 2
    assert {s["cited"] for s in sources} == {False}
    assert [s["number"] for s in sources] == list(range(1, len(sources) + 1))


async def test_only_cited_sources_are_kept(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await index(app, "guia.md", MARKDOWN.encode())
    llm.chunks = ["Según [2], y un número inventado [9]."]

    sources = sources_of(await ask(client, "vacaciones días solicitud portal empleado"))

    assert [(s["number"], s["cited"]) for s in sources] == [(2, True)]


async def test_pdf_sources_include_page(app: FastAPI, client: AsyncClient, tmp_path: Any) -> None:
    app.state.llm = FakeLLMClient(delay_seconds=0)
    await index(
        app,
        "manual.pdf",
        pdf_bytes(tmp_path, ["Portada", "Horario de oficina de 8 a 15 horas"]),
    )

    events = await ask(client, "¿Cuál es el horario de oficina?")

    answer = "".join(data["content"] for kind, data in events if kind == "token")
    assert answer.endswith("Según la documentación [1].")
    [source] = sources_of(events)
    assert (source["filename"], source["page"], source["cited"]) == ("manual.pdf", 2, True)


async def test_deleted_document_keeps_reference_without_link(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    document_id = await index(app, "guia.md", MARKDOWN.encode())
    llm.chunks = ["Vacaciones [1]."]
    events = await ask(client, "¿Cuántos días de vacaciones tengo al año?")

    await app.state.documents.delete(document_id)

    messages = (await client.get(f"/api/v1/conversations/{events[0][1]['id']}/messages")).json()
    [source] = messages[1]["sources"]
    assert source["document_id"] is None
    assert source["filename"] == "guia.md"


def test_fake_llm_cites_only_with_context() -> None:
    import asyncio

    async def answer(system: str) -> str:
        messages = [ChatMessage("system", system), ChatMessage("user", "hola")]
        return "".join([d async for d in FakeLLMClient(0).stream_chat(messages)])

    assert asyncio.run(answer("Base")) == "Respuesta simulada a: **hola**"
    assert asyncio.run(answer("Base\n[1] Fuente: x\ntexto")).endswith("[1].")


@pytest.mark.parametrize(
    ("filename", "data", "content_type"),
    [
        ("guia.md", MARKDOWN.encode(), "text/plain; charset=utf-8"),
        ("gastos.html", HTML.encode(), "text/html; charset=utf-8"),
    ],
)
async def test_public_file_view(
    app: FastAPI, client: AsyncClient, filename: str, data: bytes, content_type: str
) -> None:
    document_id = await index(app, filename, data)

    response = await client.get(f"/api/v1/documents/{document_id}/file")

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == content_type
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    if filename.endswith(".html"):
        assert response.headers["content-security-policy"].startswith("sandbox;")
    else:
        assert "content-security-policy" not in response.headers


async def test_public_pdf_view_and_missing_files(
    app: FastAPI, client: AsyncClient, tmp_path: Any
) -> None:
    document_id = await index(app, "m.pdf", pdf_bytes(tmp_path, ["Hola"]))

    response = await client.get(f"/api/v1/documents/{document_id}/file")
    assert response.headers["content-type"] == "application/pdf"

    assert (await client.get("/api/v1/documents/nope/file")).status_code == 404
    document = await app.state.documents.get(document_id)
    app.state.documents.file_path(document).unlink()
    assert (await client.get(f"/api/v1/documents/{document_id}/file")).status_code == 404


async def test_document_deleted_while_answering_keeps_the_answer(
    app: FastAPI, client: AsyncClient
) -> None:
    document_id = await index(app, "guia.md", MARKDOWN.encode())

    class DeletingLLM:
        async def stream_chat(self, messages: Any) -> Any:
            yield "Vacaciones"
            await app.state.documents.delete(document_id)
            yield " [1]."

    app.state.llm = DeletingLLM()
    events = await ask(client, "¿Cuántos días de vacaciones tengo al año?")

    assert events[-1][0] == "done"
    messages = (await client.get(f"/api/v1/conversations/{events[0][1]['id']}/messages")).json()
    assert messages[1]["content"] == "Vacaciones [1]."
    assert messages[1]["sources"][0]["document_id"] is None
