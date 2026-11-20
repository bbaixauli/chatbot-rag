import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.services.chat_config import ChatConfig, ChatConfigRepository
from tests.conftest import ScriptedLLM, set_config

CREDENTIALS = {"username": "admin", "password": "secreto"}
EMPTY: dict[str, object] = {"welcome_message": None, "suggested_questions": []}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    response = await client.post("/api/v1/admin/session", json=CREDENTIALS)
    assert response.status_code == 200
    return client


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_admin_endpoints_require_admin(client: AsyncClient, method: str) -> None:
    response = await client.request(method, "/api/v1/admin/chat-config", json=EMPTY)
    assert response.status_code == 401


async def test_empty_by_default(client: AsyncClient) -> None:
    response = await client.get("/api/v1/chat/config")
    assert response.status_code == 200
    assert response.json() == EMPTY


async def test_save_and_read_publicly(admin_client: AsyncClient) -> None:
    body = {
        "welcome_message": "  ¡Hola! Pregúntame sobre la normativa interna.  ",
        "suggested_questions": [
            " ¿Cuántos días de vacaciones tengo? ",
            "",
            "   ",
            "¿Cómo pido gastos?",
        ],
    }

    saved = await admin_client.put("/api/v1/admin/chat-config", json=body)

    expected = {
        "welcome_message": "¡Hola! Pregúntame sobre la normativa interna.",
        "suggested_questions": ["¿Cuántos días de vacaciones tengo?", "¿Cómo pido gastos?"],
    }
    assert saved.status_code == 200
    assert saved.json() == expected
    assert (await admin_client.get("/api/v1/admin/chat-config")).json() == expected
    # La configuración es pública para el chat (sin sesión de administración).
    await admin_client.delete("/api/v1/admin/session")
    assert (await admin_client.get("/api/v1/chat/config")).json() == expected


async def test_update_overwrites_and_blank_welcome_disables_it(admin_client: AsyncClient) -> None:
    await admin_client.put(
        "/api/v1/admin/chat-config",
        json={"welcome_message": "Hola", "suggested_questions": ["Una", "Dos"]},
    )

    updated = await admin_client.put(
        "/api/v1/admin/chat-config",
        json={"welcome_message": "   ", "suggested_questions": ["Tres"]},
    )

    assert updated.json() == {"welcome_message": None, "suggested_questions": ["Tres"]}
    assert (await admin_client.get("/api/v1/chat/config")).json() == updated.json()


async def test_rejects_invalid_config(app: FastAPI, admin_client: AsyncClient) -> None:
    await set_config(app, "chat", welcome_max_length=5)
    await set_config(app, "chat", suggested_question_max_length=3)
    url = "/api/v1/admin/chat-config"

    six = {"suggested_questions": ["1", "2", "3", "4", "5", "6"]}
    assert (await admin_client.put(url, json=six)).status_code == 422
    long_welcome = {"welcome_message": "123456"}
    assert (await admin_client.put(url, json=long_welcome)).status_code == 422
    long_question = {"suggested_questions": ["1234"]}
    assert (await admin_client.put(url, json=long_question)).status_code == 422
    assert (await admin_client.get(url)).json() == EMPTY


async def test_welcome_message_is_not_sent_to_the_model(
    admin_client: AsyncClient, llm: ScriptedLLM
) -> None:
    await admin_client.put(
        "/api/v1/admin/chat-config",
        json={"welcome_message": "Bienvenida", "suggested_questions": []},
    )

    await admin_client.post("/api/v1/chat", json={"message": "hola"})

    assert all("Bienvenida" not in m.content for m in llm.calls[0])


async def test_repository_ignores_malformed_values(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        repo = ChatConfigRepository(session)
        await repo.save(ChatConfig(welcome_message="", suggested_questions=list("abcdefg")), "x")
        config = await repo.get()

    assert config == ChatConfig(welcome_message=None, suggested_questions=list("abcde"))
