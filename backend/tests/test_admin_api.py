import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.api import admin
from app.core.auth import verify_admin_credentials
from app.core.config import Settings
from tests.conftest import ScriptedLLM

CREDENTIALS = {"username": "admin", "password": "secreto"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    response = await client.post("/api/v1/admin/session", json=CREDENTIALS)
    assert response.status_code == 200
    return client


async def test_session_flow(client: AsyncClient) -> None:
    anonymous = await client.get("/api/v1/admin/session")
    assert anonymous.json() == {"authenticated": False, "username": None, "enabled": True}

    login = await client.post("/api/v1/admin/session", json=CREDENTIALS)
    assert login.json() == {"authenticated": True, "username": "admin", "enabled": True}
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert (await client.get("/api/v1/admin/session")).json()["authenticated"] is True

    assert (await client.delete("/api/v1/admin/session")).status_code == 204
    assert (await client.get("/api/v1/admin/session")).json()["authenticated"] is False


@pytest.mark.parametrize(
    "credentials",
    [
        {"username": "admin", "password": "mala"},
        {"username": "otro", "password": "secreto"},
    ],
)
async def test_login_rejects_wrong_credentials(
    client: AsyncClient, credentials: dict[str, str]
) -> None:
    response = await client.post("/api/v1/admin/session", json=credentials)
    assert response.status_code == 401
    assert (await client.get("/api/v1/admin/prompt")).status_code == 401


async def test_login_disabled_without_password(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings.admin_password = None

    assert (await client.get("/api/v1/admin/session")).json()["enabled"] is False
    assert (await client.post("/api/v1/admin/session", json=CREDENTIALS)).status_code == 503
    assert verify_admin_credentials(app.state.settings, "admin", "secreto") is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/admin/prompt"),
        ("PUT", "/api/v1/admin/prompt"),
        ("GET", "/api/v1/admin/prompt/versions"),
        ("POST", "/api/v1/admin/prompt/versions/1/restore"),
    ],
)
async def test_prompt_endpoints_require_admin(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, json={"content": "x"})
    assert response.status_code == 401


async def test_default_prompt(admin_client: AsyncClient, settings: Settings) -> None:
    body = (await admin_client.get("/api/v1/admin/prompt")).json()
    assert body["content"] == settings.default_system_prompt
    assert body["is_default"] is True
    assert body["version_id"] is None
    assert (await admin_client.get("/api/v1/admin/prompt/versions")).json() == []


async def test_save_and_version_history(admin_client: AsyncClient) -> None:
    first = (await admin_client.put("/api/v1/admin/prompt", json={"content": " Uno "})).json()
    same = (await admin_client.put("/api/v1/admin/prompt", json={"content": "Uno"})).json()
    second = (await admin_client.put("/api/v1/admin/prompt", json={"content": "Dos"})).json()

    assert first["content"] == "Uno"
    assert first["is_default"] is False
    assert same["version_id"] == first["version_id"]
    assert second["version_id"] > first["version_id"]
    assert (await admin_client.get("/api/v1/admin/prompt")).json()["content"] == "Dos"

    versions = (await admin_client.get("/api/v1/admin/prompt/versions")).json()
    assert [(v["content"], v["created_by"]) for v in versions] == [
        ("Dos", "admin"),
        ("Uno", "admin"),
    ]


async def test_restore_creates_new_version(admin_client: AsyncClient) -> None:
    first = (await admin_client.put("/api/v1/admin/prompt", json={"content": "Uno"})).json()
    await admin_client.put("/api/v1/admin/prompt", json={"content": "Dos"})

    restored = await admin_client.post(
        f"/api/v1/admin/prompt/versions/{first['version_id']}/restore"
    )

    assert restored.status_code == 200
    assert restored.json()["content"] == "Uno"
    versions = (await admin_client.get("/api/v1/admin/prompt/versions")).json()
    assert [v["content"] for v in versions] == ["Uno", "Dos", "Uno"]
    missing = await admin_client.post("/api/v1/admin/prompt/versions/999/restore")
    assert missing.status_code == 404


async def test_invalid_prompts_are_rejected(app: FastAPI, admin_client: AsyncClient) -> None:
    app.state.settings.system_prompt_max_length = 5
    assert (
        await admin_client.put("/api/v1/admin/prompt", json={"content": "   "})
    ).status_code == 422
    assert (await admin_client.put("/api/v1/admin/prompt", json={"content": ""})).status_code == 422
    too_long = await admin_client.put("/api/v1/admin/prompt", json={"content": "123456"})
    assert too_long.status_code == 422


async def test_chat_uses_current_prompt(admin_client: AsyncClient, llm: ScriptedLLM) -> None:
    await admin_client.put("/api/v1/admin/prompt", json={"content": "Eres un experto en RAG."})

    await admin_client.post("/api/v1/chat", json={"message": "hola"})

    assert llm.calls[0][0].role == "system"
    assert llm.calls[0][0].content == "Eres un experto en RAG."
