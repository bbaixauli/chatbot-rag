"""Búsqueda limitada a unos documentos (#58): chat, conversaciones y lista de documentos."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Conversation, QuestionLog
from tests.conftest import ScriptedLLM, parse_sse, set_config
from tests.factories import MARKDOWN

DIETAS = """# Gastos de viaje

La dieta diaria nacional es de 53,34 euros y la internacional depende del país.

## Vacaciones y viajes

Durante las vacaciones no se abonan dietas."""


class Bytes:
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


async def _index(app: FastAPI, filename: str, content: str) -> str:
    result = await app.state.documents.upload(filename, Bytes(content.encode()), "admin")
    await app.state.tasks.drain()
    document_id: str = result.document.id
    return document_id


async def _ask(client: AsyncClient, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post("/api/v1/chat", json=body)
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


def _event(events: list[tuple[str, dict[str, Any]]], kind: str) -> dict[str, Any] | None:
    return next((data for name, data in events if name == kind), None)


def _source_documents(events: list[tuple[str, dict[str, Any]]]) -> set[str]:
    sources = (_event(events, "sources") or {}).get("sources", [])
    return {source["document_id"] for source in sources}


async def test_scope_limits_the_search_and_is_kept_in_the_conversation(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    guia = await _index(app, "guia.md", MARKDOWN)
    dietas = await _index(app, "dietas.md", DIETAS)
    question = "¿Cuántos días de vacaciones tengo al año?"

    # Sin limitar, la guía de vacaciones es la fuente.
    assert guia in _source_documents(await _ask(client, message=question))

    events = await _ask(client, message=question, document_ids=[dietas])

    assert _event(events, "scope") == {
        "documents": [{"id": dietas, "filename": "dietas.md", "title": "Gastos de viaje"}],
        "categories": [],
        "source": "selector",
    }
    assert _source_documents(events) == {dietas}
    system = llm.calls[-1][0].content
    assert "Fuente: Gastos de viaje" in system  # el título de dietas.md
    assert "Guía de vacaciones" not in system  # el de guia.md
    conversation_id = _event(events, "conversation")["id"]  # type: ignore[index]
    listed = (await client.get("/api/v1/conversations")).json()
    assert next(c for c in listed if c["id"] == conversation_id)["document_ids"] == [dietas]

    # La siguiente pregunta de la conversación mantiene la limitación…
    follow_up = await _ask(client, message=question, conversation_id=conversation_id)
    assert _event(follow_up, "scope") is not None
    assert _source_documents(follow_up) == {dietas}

    # …hasta que se quita.
    cleared = await _ask(client, message=question, conversation_id=conversation_id, document_ids=[])
    assert _event(cleared, "scope") is None
    assert guia in _source_documents(cleared)
    async with app.state.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.document_ids is None


async def test_scoped_search_ignores_the_general_minimum_score(
    app: FastAPI, client: AsyncClient
) -> None:
    dietas = await _index(app, "dietas.md", DIETAS)
    await _index(app, "guia.md", MARKDOWN)
    rag = app.state.config.current.rag
    # Una puntuación mínima tan alta deja sin fuentes la búsqueda en todo el índice…
    await set_config(app, "rag", min_score=0.99)
    assert not _source_documents(await _ask(client, message="permisos por mudanza"))

    # …pero la limitada usa la suya (0 por defecto): basta con el top-k.
    events = await _ask(client, message="permisos por mudanza", document_ids=[dietas])
    assert _source_documents(events) == {dietas}
    assert rag.scoped_min_score == 0.0


async def test_unknown_documents_are_rejected(app: FastAPI, client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/chat", json={"message": "Hola", "document_ids": ["no-existe"]}
    )

    assert response.status_code == 422
    assert "no-existe" in response.json()["detail"]
    # No se ha creado la conversación ni guardado el mensaje.
    assert (await client.get("/api/v1/conversations")).json() == []

    too_many = await client.post(
        "/api/v1/chat", json={"message": "Hola", "document_ids": [str(i) for i in range(6)]}
    )
    assert too_many.status_code == 422


async def test_deleted_documents_leave_the_scope(app: FastAPI, client: AsyncClient) -> None:
    guia = await _index(app, "guia.md", MARKDOWN)
    dietas = await _index(app, "dietas.md", DIETAS)
    events = await _ask(client, message="vacaciones", document_ids=[guia, dietas])
    conversation_id = _event(events, "conversation")["id"]  # type: ignore[index]

    await app.state.documents.delete(dietas)
    follow_up = await _ask(client, message="vacaciones", conversation_id=conversation_id)
    assert _event(follow_up, "scope")["documents"] == [  # type: ignore[index]
        {"id": guia, "filename": "guia.md", "title": "Guía de vacaciones"}
    ]

    await app.state.documents.delete(guia)
    last = await _ask(client, message="vacaciones", conversation_id=conversation_id)
    assert _event(last, "scope") is None
    async with app.state.sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.document_ids is None


async def test_scope_is_recorded_in_the_question_log(app: FastAPI, client: AsyncClient) -> None:
    dietas = await _index(app, "dietas.md", DIETAS)

    await _ask(client, message="¿Cuánto es la dieta?", document_ids=[dietas])
    await _ask(client, message="¿Cuánto es la dieta?")

    async with app.state.sessionmaker() as session:
        rows = list(await session.scalars(select(QuestionLog).order_by(QuestionLog.id)))
    assert [(r.scope_document_ids, r.scope_source) for r in rows] == [
        ([dietas], "selector"),
        (None, None),
    ]


async def test_update_conversation_scope(app: FastAPI, client: AsyncClient) -> None:
    dietas = await _index(app, "dietas.md", DIETAS)
    created = (await client.post("/api/v1/conversations", json={})).json()
    assert created["document_ids"] == []
    url = f"/api/v1/conversations/{created['id']}"

    updated = await client.patch(url, json={"document_ids": [dietas]})
    assert updated.status_code == 200
    assert updated.json()["document_ids"] == [dietas]
    assert updated.json()["title"] == created["title"]

    renamed = await client.patch(url, json={"title": "Dietas"})
    assert renamed.json() == {**renamed.json(), "title": "Dietas", "document_ids": [dietas]}

    rejected = await client.patch(url, json={"document_ids": ["no-existe"]})
    assert rejected.status_code == 422
    blank = await client.patch(url, json={"title": "  "})
    assert blank.status_code == 422

    cleared = await client.patch(url, json={"document_ids": []})
    assert cleared.json()["document_ids"] == []


async def test_public_list_of_indexed_documents(app: FastAPI, client: AsyncClient) -> None:
    await _index(app, "Normativa.md", MARKDOWN)
    await _index(app, "dietas.md", DIETAS)
    broken = await app.state.documents.upload("roto.pdf", Bytes(b"no es un pdf"), "admin")
    await app.state.tasks.drain()

    response = await client.get("/api/v1/documents")

    assert response.status_code == 200
    listed = response.json()
    # Por título (el primer encabezado de cada Markdown).
    assert [(d["filename"], d["title"]) for d in listed] == [
        ("dietas.md", "Gastos de viaje"),
        ("Normativa.md", "Guía de vacaciones"),
    ]
    assert set(listed[0]) == {"id", "filename", "title", "kind", "category", "category_id"}
    assert broken.document.id not in {d["id"] for d in listed}


async def test_answers_keep_where_they_searched(app: FastAPI, client: AsyncClient) -> None:
    dietas = await _index(app, "dietas.md", DIETAS)
    events = await _ask(client, message="¿Cuánto es la dieta?", document_ids=[dietas])
    conversation_id = _event(events, "conversation")["id"]  # type: ignore[index]
    await _ask(
        client, message="¿Y en vacaciones?", conversation_id=conversation_id, document_ids=[]
    )

    messages = (await client.get(f"/api/v1/conversations/{conversation_id}/messages")).json()

    assert [(m["role"], m["scope"]) for m in messages] == [
        ("user", None),
        (
            "assistant",
            {
                "documents": [{"id": dietas, "filename": "dietas.md", "title": "Gastos de viaje"}],
                "categories": [],
                "source": "selector",
            },
        ),
        ("user", None),
        ("assistant", None),
    ]
