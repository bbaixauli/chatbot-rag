"""Detección de los documentos que se nombran en una pregunta (#58, fase 2)."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.services.document_names import (
    detect_documents,
    document_codes,
    normalize,
    text_codes,
    title_variants,
)
from app.services.scope import ScopeDocument
from tests.conftest import parse_sse, set_config
from tests.factories import MARKDOWN

NOR = ScopeDocument("nor", "NOR-013 CONTROL DE ACCESO LOGICO EN DTIC v3.2.report_0.2.pdf")
PRO = ScopeDocument("pro", "PRO_02 Gestión de incidencias.pdf")
GUIA = ScopeDocument("guia", "guia.md")
PLAN = ScopeDocument("plan", "Plan de vacaciones 2025.pdf")
DOCUMENTS = [NOR, PRO, GUIA, PLAN]


def test_normalize() -> None:
    assert normalize("¿Qué dice el NOR-013, «Control»?") == "que dice el nor 013 control"


def test_codes() -> None:
    assert document_codes(NOR.filename) == {("nor", 13)}
    assert document_codes(PRO.filename) == {("pro", 2)}
    # Solo en mayúsculas: «Plan de vacaciones 2025» no tiene código.
    assert document_codes(PLAN.filename) == set()
    assert text_codes("el nor 13 y el PRO-002") == {("nor", 13), ("pro", 2)}


def test_title_variants() -> None:
    assert title_variants(NOR.filename) >= {
        "nor 013 control de acceso logico en dtic",
        "control de acceso logico en dtic",
    }
    assert title_variants(GUIA.filename) == set()  # demasiado corto para reconocerlo


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("resúmeme el NOR-013", ["nor"]),
        ("¿qué dice la nor 13?", ["nor"]),
        ("NOR013", ["nor"]),
        ("¿Cómo es el control de acceso lógico en DTIC?", ["nor"]),
        ("Explícame la gestión de incidencias", ["pro"]),
        ("¿Qué cambia entre el PRO-2 y el NOR-013?", ["nor", "pro"]),
        ("¿Qué días de vacaciones hay en 2025?", []),
        ("¿qué dice la guía?", []),
        ("control de acceso", []),
        ("¿Y el NOR-014?", []),
    ],
)
def test_detect_documents(question: str, expected: list[str]) -> None:
    assert [d.id for d in detect_documents([question], DOCUMENTS)] == expected


def test_detection_uses_the_rewritten_question_and_rejects_ambiguous_ones() -> None:
    assert detect_documents(
        ["¿y las bajas?", "¿Cómo se tramitan las bajas en el NOR-013?"], DOCUMENTS
    ) == [NOR]
    many = [ScopeDocument(str(i), f"NOR-{i:03d} Normativa.pdf") for i in range(1, 8)]
    question = " ".join(f"NOR-{i:03d}" for i in range(1, 8))
    assert detect_documents([question], many) == []
    assert len(detect_documents([question], many[:5])) == 5


# --- En el chat --------------------------------------------------------------------------

NORMA = """# Control de acceso

Las cuentas de usuario se bloquean tras 5 intentos fallidos. Las altas se piden al CAU."""


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


def _scope(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    return next((data for kind, data in events if kind == "scope"), None)


async def test_chat_limits_the_search_to_the_named_document(
    app: FastAPI, client: AsyncClient
) -> None:
    norma = await _index(app, "NOR-013 Control de acceso.md", NORMA)
    guia = await _index(app, "guia.md", MARKDOWN)

    events = await _ask(client, message="¿Qué dice el NOR-013 sobre las vacaciones?")

    assert _scope(events) == {
        "documents": [
            {"id": norma, "filename": "NOR-013 Control de acceso.md", "title": "Control de acceso"}
        ],
        "categories": [],
        "source": "detected",
    }
    sources = next(data["sources"] for kind, data in events if kind == "sources")
    assert {s["document_id"] for s in sources} == {norma}
    # No se guarda en la conversación: la siguiente pregunta busca en todos.
    conversation_id = events[0][1]["id"]
    follow_up = await _ask(
        client, message="¿Cuántos días de vacaciones tengo?", conversation_id=conversation_id
    )
    assert _scope(follow_up) is None
    sources = next(data["sources"] for kind, data in follow_up if kind == "sources")
    assert guia in {s["document_id"] for s in sources}


async def test_selected_documents_take_priority(app: FastAPI, client: AsyncClient) -> None:
    await _index(app, "NOR-013 Control de acceso.md", NORMA)
    guia = await _index(app, "guia.md", MARKDOWN)

    events = await _ask(client, message="¿Qué dice el NOR-013?", document_ids=[guia])

    assert _scope(events) == {
        "documents": [{"id": guia, "filename": "guia.md", "title": "Guía de vacaciones"}],
        "categories": [],
        "source": "selector",
    }


async def test_detection_can_be_disabled(app: FastAPI, client: AsyncClient) -> None:
    await _index(app, "NOR-013 Control de acceso.md", NORMA)
    await set_config(app, "rag", detect_document_names=False)

    assert _scope(await _ask(client, message="¿Qué dice el NOR-013?")) is None


async def test_follow_up_questions_use_the_rewritten_question(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    norma = await _index(app, "NOR-013 Control de acceso.md", NORMA)
    first = await _ask(client, message="¿Qué dice el NOR-013?")

    async def rewrite(*_: Any) -> str:
        return "¿Cómo se piden las altas según el NOR-013?"

    monkeypatch.setattr("app.services.chat.condense_question", rewrite)
    follow_up = await _ask(client, message="¿Y las altas?", conversation_id=first[0][1]["id"])

    assert _scope(follow_up) == {
        "documents": [
            {"id": norma, "filename": "NOR-013 Control de acceso.md", "title": "Control de acceso"}
        ],
        "categories": [],
        "source": "detected",
    }
