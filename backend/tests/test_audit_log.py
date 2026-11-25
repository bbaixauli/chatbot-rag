"""Registro de auditoría (#50): quién, desde dónde y con qué se respondió cada pregunta."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app.api import admin
from app.core.config import Settings
from app.db.models import QuestionLog
from tests.conftest import ScriptedLLM, parse_sse
from tests.factories import MARKDOWN
from tests.test_chat_rag import index


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.rag_min_score = 0.2
    settings.chunk_size_chars = 300
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


async def rows(app: FastAPI) -> list[QuestionLog]:
    async with app.state.sessionmaker() as db:
        return list(await db.scalars(select(QuestionLog).order_by(QuestionLog.id)))


async def ask(client: AsyncClient, question: str, **headers: str) -> str:
    response = await client.post("/api/v1/chat", json={"message": question}, headers=headers)
    assert response.status_code == 200, response.text
    return str(parse_sse(response.text)[0][1]["id"])


async def test_records_who_asked_from_where_and_with_what(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await index(app, "vacaciones.md", MARKDOWN.encode())
    llm.chunks = ["Hay 23 días de vacaciones [1]."]

    await ask(client, "¿Cuántos días de vacaciones?", **{"user-agent": "Navegador/1.0"})

    [row] = await rows(app)
    assert row.client_ip == "127.0.0.1"
    assert row.user_agent == "Navegador/1.0"
    assert row.user_display_name == "Anónimo"
    assert row.model == app.state.config.current.llm.model
    assert row.prompt_version_id is None  # prompt por defecto, sin versiones guardadas
    assert row.search_query == "¿Cuántos días de vacaciones?"
    assert row.error_detail is None
    assert row.cited_documents
    first = row.cited_documents[0]
    assert first["filename"] == "vacaciones.md"
    assert first["title"] == "Guía de vacaciones"  # primer encabezado del Markdown
    assert first["cited"] is True
    assert set(first) == {"document_id", "filename", "title", "page", "cited"}


async def test_records_the_prompt_version_in_use(app: FastAPI, client: AsyncClient) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as admin_c:
        await admin_c.post(
            "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
        )
        version = (
            await admin_c.put("/api/v1/admin/prompt", json={"content": "Eres un experto."})
        ).json()["version_id"]

    await ask(client, "Hola")

    [row] = await rows(app)
    assert row.prompt_version_id == version


async def test_long_user_agents_are_trimmed(app: FastAPI, client: AsyncClient) -> None:
    await ask(client, "Hola", **{"user-agent": "x" * 1000})
    [row] = await rows(app)
    assert row.user_agent == "x" * 300


async def test_records_why_an_answer_failed(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    llm.fail = True
    await ask(client, "Pregunta con fallo")
    llm.fail = False
    llm.chunks = [" "]
    await ask(client, "Pregunta con respuesta vacía")

    failed, empty = await rows(app)
    assert failed.error_detail == "caído"
    assert empty.error_detail == "El modelo devolvió una respuesta vacía"
    # Sin documentos indexados no se busca nada.
    assert failed.cited_documents is None


async def test_records_a_conversation_deleted_while_answering(
    app: FastAPI, settings: Settings, llm: ScriptedLLM
) -> None:
    from app.services.chat import ChatService, Requester

    service = ChatService(app.state.sessionmaker, llm, settings, app.state.config.current)
    prepared = await service.prepare(
        "anonymous", None, "¿Algo?", requester=Requester(ip="10.0.0.7", display_name="Ana")
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.delete(f"/api/v1/conversations/{prepared.conversation_id}")
    events = [event async for event in service.stream(prepared)]

    assert any("ya no existe" in event for event in events)
    [row] = await rows(app)
    assert (row.client_ip, row.user_display_name) == ("10.0.0.7", "Ana")
    assert row.error_detail == "La conversación se borró mientras se respondía"
    assert row.answer == "Hola, mundo"
