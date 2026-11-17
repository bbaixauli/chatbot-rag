from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.models import Conversation, Message, MessageRole
from app.services.conversations import ConversationRepository


async def test_empty_list(client: AsyncClient) -> None:
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_with_default_and_custom_title(client: AsyncClient) -> None:
    default = await client.post("/api/v1/conversations", json={})
    custom = await client.post("/api/v1/conversations", json={"title": "  Contratos  "})

    assert default.status_code == 201
    assert default.json()["title"] == "Nueva conversación"
    assert custom.json()["title"] == "Contratos"


async def test_list_orders_by_most_recent_activity(client: AsyncClient) -> None:
    first = (await client.post("/api/v1/conversations", json={"title": "A"})).json()
    await client.post("/api/v1/conversations", json={"title": "B"})
    await client.patch(f"/api/v1/conversations/{first['id']}", json={"title": "A2"})

    titles = [c["title"] for c in (await client.get("/api/v1/conversations")).json()]
    assert titles == ["A2", "B"]


async def test_rename(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversations", json={})).json()

    response = await client.patch(
        f"/api/v1/conversations/{created['id']}", json={"title": " Nuevo título "}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Nuevo título"


async def test_rename_rejects_blank_title(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversations", json={})).json()
    response = await client.patch(f"/api/v1/conversations/{created['id']}", json={"title": "  "})
    assert response.status_code == 422


async def test_unknown_conversation_returns_404(client: AsyncClient) -> None:
    assert (await client.patch("/api/v1/conversations/x", json={"title": "t"})).status_code == 404
    assert (await client.delete("/api/v1/conversations/x")).status_code == 404
    assert (await client.get("/api/v1/conversations/x/messages")).status_code == 404


async def test_other_users_conversations_are_hidden(app: FastAPI, client: AsyncClient) -> None:
    async with app.state.sessionmaker() as session:
        other = await ConversationRepository(session).create("otro-usuario", "Privada")
        await session.commit()

    assert (await client.get("/api/v1/conversations")).json() == []
    assert (await client.get(f"/api/v1/conversations/{other.id}/messages")).status_code == 404


async def test_messages_and_cascade_delete(app: FastAPI, client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversations", json={})).json()
    async with app.state.sessionmaker() as session:
        repo = ConversationRepository(session)
        conversation = await repo.get_for_user(created["id"], "anonymous")
        assert conversation is not None
        await repo.add_message(conversation, MessageRole.USER, "pregunta")
        await repo.add_message(conversation, MessageRole.ASSISTANT, "respuesta")
        await session.commit()

    messages = (await client.get(f"/api/v1/conversations/{created['id']}/messages")).json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "pregunta"),
        ("assistant", "respuesta"),
    ]

    assert (await client.delete(f"/api/v1/conversations/{created['id']}")).status_code == 204
    async with app.state.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 0
