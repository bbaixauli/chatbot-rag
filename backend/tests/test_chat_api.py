from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.services.chat import ChatService
from tests.conftest import ScriptedLLM, parse_sse


async def _chat(
    client: AsyncClient, message: str, conversation_id: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(
        "/api/v1/chat", json={"message": message, "conversation_id": conversation_id}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


async def test_new_conversation_streams_and_persists(client: AsyncClient, llm: ScriptedLLM) -> None:
    events = await _chat(client, "  ¿Qué es un RAG?  ")

    kinds = [kind for kind, _ in events]
    assert kinds == ["conversation", "token", "token", "token", "done"]
    conversation = events[0][1]
    assert conversation["title"] == "¿Qué es un RAG?"
    assert "".join(data["content"] for kind, data in events if kind == "token") == "Hola, mundo"

    messages = (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "¿Qué es un RAG?"),
        ("assistant", "Hola, mundo"),
    ]
    assert events[-1][1]["message_id"] == messages[1]["id"]
    assert conversation["user_message_id"] == messages[0]["id"]

    prompt = llm.calls[0]
    assert prompt[0].role == "system"
    assert "español" in prompt[0].content
    assert (prompt[1].role, prompt[1].content) == ("user", "¿Qué es un RAG?")


async def test_existing_conversation_sends_history(client: AsyncClient, llm: ScriptedLLM) -> None:
    first = await _chat(client, "Primera")
    conversation_id = first[0][1]["id"]

    await _chat(client, "Segunda", conversation_id)

    prompt = llm.calls[1]
    assert [(m.role, m.content) for m in prompt[1:]] == [
        ("user", "Primera"),
        ("assistant", "Hola, mundo"),
        ("user", "Segunda"),
    ]
    assert len((await client.get("/api/v1/conversations")).json()) == 1


async def test_history_is_limited(app: FastAPI, client: AsyncClient, llm: ScriptedLLM) -> None:
    app.state.settings.chat_history_max_messages = 2
    conversation_id = (await _chat(client, "uno"))[0][1]["id"]
    await _chat(client, "dos", conversation_id)

    # 2 mensajes de historial + la pregunta actual
    assert [m.content for m in llm.calls[1][1:]] == ["uno", "Hola, mundo", "dos"]

    app.state.settings.chat_history_max_messages = 1
    await _chat(client, "tres", conversation_id)
    assert [m.content for m in llm.calls[2][1:]] == ["Hola, mundo", "tres"]


async def test_unknown_conversation_returns_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/chat", json={"message": "hola", "conversation_id": "x"})
    assert response.status_code == 404


async def test_blank_and_too_long_messages_are_rejected(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings.chat_max_message_length = 5
    assert (await client.post("/api/v1/chat", json={"message": "   "})).status_code == 422
    assert (await client.post("/api/v1/chat", json={"message": ""})).status_code == 422
    assert (await client.post("/api/v1/chat", json={"message": "123456"})).status_code == 422


async def test_llm_error_emits_error_event_and_skips_answer(
    client: AsyncClient, llm: ScriptedLLM
) -> None:
    llm.fail = True
    events = await _chat(client, "hola")

    assert events[-1][0] == "error"
    assert "no está disponible" in events[-1][1]["detail"]
    messages = (await client.get(f"/api/v1/conversations/{events[0][1]['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user"]


async def test_conversation_deleted_while_streaming(app: FastAPI, settings: Settings) -> None:
    service = ChatService(app.state.sessionmaker, ScriptedLLM(), settings)
    prepared = await service.prepare("anonymous", None, "hola")
    stream = service.stream(prepared)
    await anext(stream)  # evento `conversation`

    async with app.state.sessionmaker() as session:
        from app.db.models import Conversation

        conversation = await session.get(Conversation, prepared.conversation_id)
        await session.delete(conversation)
        await session.commit()

    remaining = [chunk async for chunk in stream]
    assert remaining[-1].startswith("event: error")
