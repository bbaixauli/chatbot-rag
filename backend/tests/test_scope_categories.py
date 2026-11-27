"""Búsqueda del chat limitada por categoría (#85)."""

import csv
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings
from app.core.runtime_config import RagConfig
from app.db.models import Category, Conversation, Document, QuestionLog
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.rag import min_score_for
from app.services.scope import SearchedIn
from app.services.vectorstore import EXACT_SEARCH_MAX_DOCUMENTS, ChromaVectorStore, exact_search
from tests.conftest import ScriptedLLM, parse_sse, set_config

CHAT = "/api/v1/chat"
CONVERSATIONS = "/api/v1/conversations"

DIETAS = "# Gastos de viaje\n\nLa dieta diaria nacional es de 53,34 euros."
VACACIONES = "# Vacaciones\n\nHay 23 días laborables de vacaciones al año."
TELETRABAJO = "# Teletrabajo\n\nSe puede teletrabajar dos días por semana."


class Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.rag_min_score = 0.0
    settings.admin_password = SecretStr("secreto")
    return settings


async def _category(app: FastAPI, name: str) -> int:
    async with app.state.sessionmaker() as db:
        category = Category(name=name)
        db.add(category)
        await db.commit()
        return category.id


async def _index(app: FastAPI, filename: str, content: str, category_id: int | None = None) -> str:
    result = await app.state.documents.upload(
        filename, Bytes(content.encode()), "admin", category_id=category_id
    )
    await app.state.tasks.drain()
    document_id: str = result.document.id
    return document_id


async def _move(app: FastAPI, document_id: str, category_id: int | None) -> None:
    async with app.state.sessionmaker() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        document.category_id = category_id
        await db.commit()


async def _ask(client: AsyncClient, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(CHAT, json={"message": "¿Qué dice?", **body})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


def _event(events: list[tuple[str, dict[str, Any]]], kind: str) -> dict[str, Any] | None:
    return next((data for name, data in events if name == kind), None)


async def _log(app: FastAPI) -> list[QuestionLog]:
    async with app.state.sessionmaker() as db:
        return list(await db.scalars(select(QuestionLog).order_by(QuestionLog.id)))


async def _conversation(app: FastAPI, conversation_id: str) -> Conversation:
    async with app.state.sessionmaker() as db:
        conversation: Conversation | None = await db.get(Conversation, conversation_id)
        assert conversation is not None
        return conversation


async def test_a_category_limits_the_search_to_its_current_documents(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    rrhh = await _category(app, "Recursos humanos")
    dietas = await _index(app, "dietas.md", DIETAS, rrhh)
    vacaciones = await _index(app, "vacaciones.md", VACACIONES)

    events = await _ask(client, category_ids=[rrhh])

    assert _event(events, "scope") == {
        "documents": [],
        "categories": [{"id": rrhh, "name": "Recursos humanos"}],
        "source": "selector",
    }
    sources = (_event(events, "sources") or {})["sources"]
    assert {s["document_id"] for s in sources} == {dietas}
    conversation_id = (_event(events, "conversation") or {})["id"]
    assert (await _conversation(app, conversation_id)).category_ids == [rrhh]

    # La categoría se guarda, no sus documentos: el que entra después también cuenta.
    await _move(app, vacaciones, rrhh)
    await _ask(client, conversation_id=conversation_id)

    first, second = await _log(app)
    assert (first.scope_category_ids, first.scope_document_ids) == ([rrhh], [dietas])
    assert second.scope_category_ids == [rrhh]
    assert set(second.scope_document_ids or []) == {dietas, vacaciones}
    assert second.scope_source == "selector"
    # Con un solo documento, como al elegirlo suelto, se envía completo.
    assert "Gastos de viaje" in llm.calls[0][0].content


async def test_categories_and_documents_are_combined(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    dietas = await _index(app, "dietas.md", DIETAS, rrhh)
    vacaciones = await _index(app, "vacaciones.md", VACACIONES, rrhh)
    teletrabajo = await _index(app, "teletrabajo.md", TELETRABAJO)

    events = await _ask(client, category_ids=[rrhh, rrhh], document_ids=[teletrabajo, dietas])

    scope = _event(events, "scope") or {}
    assert [d["id"] for d in scope["documents"]] == [teletrabajo, dietas]
    assert [c["id"] for c in scope["categories"]] == [rrhh]
    [row] = await _log(app)
    # La unión, sin repetir.
    assert sorted(row.scope_document_ids or []) == sorted([teletrabajo, dietas, vacaciones])


async def test_a_category_has_no_document_limit(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    ids = [
        await _index(app, f"doc{n}.md", f"# Doc {n}\n\nTexto {n} único.", rrhh) for n in range(8)
    ]

    await _ask(client, category_ids=[rrhh])

    [row] = await _log(app)
    assert sorted(row.scope_document_ids or []) == sorted(ids)
    # Documentos sueltos: como mucho 5.
    response = await client.post(CHAT, json={"message": "Hola", "document_ids": ids})
    assert response.status_code == 422


async def test_empty_and_deleted_categories(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    vacia = await _category(app, "Vacía")
    await _index(app, "dietas.md", DIETAS, rrhh)

    # Una categoría sin documentos no limita la búsqueda (pero se conserva).
    events = await _ask(client, category_ids=[vacia])
    assert _event(events, "scope") is None
    conversation_id = (_event(events, "conversation") or {})["id"]
    assert (await _conversation(app, conversation_id)).category_ids == [vacia]

    # Una categoría borrada se quita de la limitación.
    patched = await client.patch(
        f"{CONVERSATIONS}/{conversation_id}", json={"category_ids": [rrhh, vacia]}
    )
    assert patched.json()["category_ids"] == [rrhh, vacia]
    async with app.state.sessionmaker() as db:
        category = await db.get(Category, rrhh)
        await db.delete(category)
        await db.commit()
    events = await _ask(client, conversation_id=conversation_id)
    assert _event(events, "scope") is None
    assert (await _conversation(app, conversation_id)).category_ids == [vacia]

    # Quitarlas.
    cleared = await client.patch(f"{CONVERSATIONS}/{conversation_id}", json={"category_ids": []})
    assert cleared.json()["category_ids"] == []


async def test_unknown_categories_are_rejected(app: FastAPI, client: AsyncClient) -> None:
    response = await client.post(CHAT, json={"message": "Hola", "category_ids": [999]})
    assert response.status_code == 422
    assert "Categorías no disponibles: 999" in response.text

    created = await client.post(CONVERSATIONS, json={})
    url = f"{CONVERSATIONS}/{created.json()['id']}"
    assert (await client.patch(url, json={"category_ids": [999]})).status_code == 422


async def test_answers_show_the_current_category_name(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    await _index(app, "dietas.md", DIETAS, rrhh)
    events = await _ask(client, category_ids=[rrhh])
    conversation_id = (_event(events, "conversation") or {})["id"]
    async with app.state.sessionmaker() as db:
        category = await db.get(Category, rrhh)
        assert category is not None
        category.name = "Personas"
        await db.commit()

    messages = (await client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()

    assert messages[1]["scope"]["categories"] == [{"id": rrhh, "name": "Personas"}]
    # Si se borra, el nombre que tenía.
    async with app.state.sessionmaker() as db:
        await db.delete(await db.get(Category, rrhh))
        await db.commit()
    messages = (await client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()
    assert messages[1]["scope"]["categories"] == [{"id": rrhh, "name": "Recursos humanos"}]


async def test_indexed_documents_include_their_category(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    await _index(app, "dietas.md", DIETAS, rrhh)

    [document] = (await client.get("/api/v1/documents")).json()

    assert (document["category"], document["category_id"]) == ("Recursos humanos", rrhh)


async def test_audit_shows_where_each_question_searched(app: FastAPI, client: AsyncClient) -> None:
    rrhh = await _category(app, "Recursos humanos")
    await _index(app, "dietas.md", DIETAS, rrhh)
    teletrabajo = await _index(app, "teletrabajo.md", TELETRABAJO)
    await _ask(client, category_ids=[rrhh])
    await _ask(client, document_ids=[teletrabajo])
    await _ask(client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as admin:
        await admin.post("/api/v1/admin/session", json={"username": "admin", "password": "secreto"})
        entries = (await admin.get("/api/v1/admin/audit/log")).json()["items"]
        exported = (await admin.get("/api/v1/admin/audit/log.csv")).text

    assert [entry["searched_in"] for entry in entries] == [
        None,
        {"source": "selector", "categories": [], "documents": ["Teletrabajo"], "document_count": 1},
        {
            "source": "selector",
            "categories": ["Recursos humanos"],
            "documents": [],
            "document_count": 1,
        },
    ]
    rows = list(csv.reader(io.StringIO(exported)))
    column = rows[0].index("Búsqueda limitada a")
    assert [row[column] for row in rows[1:]] == [
        "",
        "Teletrabajo",
        "Recursos humanos (1 documentos)",
    ]


def test_searched_in_label() -> None:
    detected = SearchedIn("detected", [], ["NOR-013"], 1)
    assert detected.label == "NOR-013 (nombrados en la pregunta)"
    both = SearchedIn("selector", ["RR. HH.", "Normativa"], [], 12)
    assert both.label == "RR. HH., Normativa (12 documentos)"


# --- Búsqueda en muchos documentos -----------------------------------------------------------


def test_minimum_score_of_a_large_scope() -> None:
    rag = RagConfig.model_validate(
        RagConfig.from_settings(Settings(rag_min_score=0.4, rag_scoped_min_score=0.1))
    )
    assert min_score_for(rag, None) == 0.4
    assert min_score_for(rag, ["a"] * 5) == 0.1
    # Una categoría grande: la puntuación general.
    assert min_score_for(rag, ["a"] * 6) == 0.4


async def test_large_scopes_use_the_index_with_a_filter(tmp_path: Path) -> None:
    assert exact_search(["a"] * EXACT_SEARCH_MAX_DOCUMENTS)
    assert not exact_search(["a"] * (EXACT_SEARCH_MAX_DOCUMENTS + 1))
    assert not exact_search(None)

    store = ChromaVectorStore(tmp_path / "chroma")
    embeddings = FakeEmbeddings()
    ids = [f"d{n}" for n in range(EXACT_SEARCH_MAX_DOCUMENTS + 10)]
    for document_id in ids:
        text = f"Normativa {document_id} sobre accesos"
        await store.add(
            document_id,
            f"{document_id}.md",
            [Chunk(0, text, None, None)],
            await embeddings.embed([text]),
        )
    [vector] = await embeddings.embed(["Normativa d3 sobre accesos"])
    subset = ids[: EXACT_SEARCH_MAX_DOCUMENTS + 5]

    hits = await store.search(vector, 5, subset)

    assert len(hits) == 5
    assert {hit.document_id for hit in hits} <= set(subset)
    assert hits[0].document_id == "d3"
    assert await store.search(vector, 0, subset) == []


async def test_scoped_search_settings_still_apply_to_categories(
    app: FastAPI, client: AsyncClient
) -> None:
    await set_config(app, "rag", scoped_min_score=0.99)
    rrhh = await _category(app, "Recursos humanos")
    await _index(app, "dietas.md", DIETAS, rrhh)
    await _index(app, "vacaciones.md", VACACIONES, rrhh)

    events = await _ask(client, category_ids=[rrhh])

    # Dos documentos: la puntuación de la búsqueda limitada, que no supera ninguno.
    assert (_event(events, "sources") or {"sources": []})["sources"] == []
