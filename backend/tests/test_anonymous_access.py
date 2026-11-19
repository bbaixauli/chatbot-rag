"""Acceso anónimo al chat con LDAP, permitido desde la administración (#30)."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.requests import Request

from app.api import admin, auth
from app.core.auth import ANONYMOUS_SESSION_PREFIX, session_user
from app.core.config import Settings
from app.db.models import Conversation
from tests.conftest import parse_sse
from tests.ldap_fixtures import PASSWORDS, ldap_settings, mock_authenticator

SESSION = "/api/v1/auth/session"
ANONYMOUS = "/api/v1/auth/anonymous"
ACCESS = "/api/v1/admin/access"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return ldap_settings(settings)


@pytest.fixture(autouse=True)
def directory(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.ldap = mock_authenticator(settings)
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_SECONDS", 0)
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


def new_client(app: FastAPI) -> AsyncClient:
    """Otro navegador: su propia cookie de sesión."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def admin_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with new_client(app) as client:
        response = await client.post(
            SESSION, json={"username": "ana", "password": PASSWORDS["ana"]}
        )
        assert response.json()["user"]["is_admin"] is True
        yield client


async def allow_anonymous(admin_client: AsyncClient, allowed: bool) -> None:
    response = await admin_client.put(ACCESS, json={"anonymous_allowed": allowed})
    assert response.status_code == 200, response.text
    assert response.json() == {"anonymous_allowed": allowed, "available": True}


async def enter_anonymously(client: AsyncClient) -> str:
    response = await client.post(ANONYMOUS)
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["is_anonymous"] is True
    assert user["is_admin"] is False
    assert user["display_name"] == "Anónimo"
    assert user["id"].startswith(ANONYMOUS_SESSION_PREFIX)
    return str(user["id"])


async def ask(client: AsyncClient, question: str) -> str:
    response = await client.post("/api/v1/chat", json={"message": question})
    assert response.status_code == 200, response.text
    return str(parse_sse(response.text)[0][1]["id"])


async def titles(client: AsyncClient) -> list[str]:
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200, response.text
    return [c["title"] for c in response.json()]


# --- Sin el check no hay acceso anónimo ---


async def test_anonymous_access_is_disabled_by_default(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    assert (await client.get(SESSION)).json()["anonymous_allowed"] is False
    assert (await admin_client.get(ACCESS)).json() == {
        "anonymous_allowed": False,
        "available": True,
    }

    response = await client.post(ANONYMOUS)

    assert response.status_code == 403
    assert (await client.get(SESSION)).json()["authenticated"] is False
    assert (await client.get("/api/v1/conversations")).status_code == 401
    assert (await client.post("/api/v1/chat", json={"message": "hola"})).status_code == 401


async def test_disabling_the_check_ends_open_anonymous_sessions(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    await allow_anonymous(admin_client, True)
    await enter_anonymously(client)
    await ask(client, "Pregunta anónima")

    await allow_anonymous(admin_client, False)

    assert (await client.get("/api/v1/conversations")).status_code == 401
    assert (await client.post("/api/v1/chat", json={"message": "otra"})).status_code == 401
    session = (await client.get(SESSION)).json()
    assert session["authenticated"] is False
    assert session["anonymous_allowed"] is False
    assert (await client.post(ANONYMOUS)).status_code == 403
    # Volver a permitirlo no reactiva la sesión anterior (se descartó).
    await allow_anonymous(admin_client, True)
    assert (await client.get("/api/v1/conversations")).status_code == 401


async def test_only_admins_change_the_check(app: FastAPI, client: AsyncClient) -> None:
    assert (await client.put(ACCESS, json={"anonymous_allowed": True})).status_code == 401
    await client.post(SESSION, json={"username": "luis", "password": PASSWORDS["luis"]})
    assert (await client.put(ACCESS, json={"anonymous_allowed": True})).status_code == 401
    assert (await client.get(ACCESS)).status_code == 401


async def test_without_ldap_there_is_no_anonymous_login(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    await allow_anonymous(admin_client, True)
    app.state.settings.auth_mode = "none"

    assert (await client.post(ANONYMOUS)).status_code == 409
    assert (await client.get(SESSION)).json()["anonymous_allowed"] is False
    # Sin LDAP la casilla no se ofrece ni se puede cambiar (el chat ya es anónimo).
    assert (await admin_client.get(ACCESS)).json()["available"] is False
    assert (await admin_client.put(ACCESS, json={"anonymous_allowed": False})).status_code == 409


# --- Con el check: cada acceso, solo sus conversaciones ---


async def test_anonymous_user_can_chat(client: AsyncClient, admin_client: AsyncClient) -> None:
    await allow_anonymous(admin_client, True)
    assert (await client.get(SESSION)).json()["anonymous_allowed"] is True

    user_id = await enter_anonymously(client)
    await ask(client, "Pregunta anónima")

    assert await titles(client) == ["Pregunta anónima"]
    session = (await client.get(SESSION)).json()
    assert session["authenticated"] is True
    assert session["user"]["id"] == user_id
    # No administra.
    assert (await client.get("/api/v1/admin/prompt")).status_code == 401


async def test_simultaneous_anonymous_users_only_see_their_conversations(
    app: FastAPI, admin_client: AsyncClient
) -> None:
    await allow_anonymous(admin_client, True)
    async with new_client(app) as first, new_client(app) as second:
        first_id = await enter_anonymously(first)
        second_id = await enter_anonymously(second)
        assert first_id != second_id

        conversation = await ask(first, "Pregunta de la primera persona")
        await ask(second, "Pregunta de la segunda persona")

        assert await titles(first) == ["Pregunta de la primera persona"]
        assert await titles(second) == ["Pregunta de la segunda persona"]
        other = f"/api/v1/conversations/{conversation}"
        assert (await second.get(f"{other}/messages")).status_code == 404
        assert (await second.delete(other)).status_code == 404
        hijack = await second.post(
            "/api/v1/chat", json={"message": "hola", "conversation_id": conversation}
        )
        assert hijack.status_code == 404
        # Los usuarios del directorio tampoco ven las conversaciones anónimas.
        assert await titles(admin_client) == []


async def test_a_new_anonymous_access_starts_without_conversations(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    await allow_anonymous(admin_client, True)
    first_id = await enter_anonymously(client)
    await ask(client, "Pregunta de la primera sesión")

    assert (await client.delete(SESSION)).status_code == 204
    assert (await client.get("/api/v1/conversations")).status_code == 401
    second_id = await enter_anonymously(client)

    assert second_id != first_id
    assert await titles(client) == []
    # Entrar de nuevo sin salir también es un acceso nuevo.
    await ask(client, "Pregunta de la segunda sesión")
    await enter_anonymously(client)
    assert await titles(client) == []

    # Las conversaciones se conservan para auditoría, con el id de cada acceso.
    async with app.state.sessionmaker() as db:
        owners = set(await db.scalars(select(Conversation.user_id)))
    assert first_id in owners
    assert second_id in owners


async def test_anonymous_login_is_rate_limited(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    await allow_anonymous(admin_client, True)
    # El límite es por IP y compartido con el resto de inicios de sesión: el del
    # administrador (misma IP en los tests) ya cuenta como uno.
    app.state.settings.rate_limit_login_per_minute = 2

    assert (await client.post(ANONYMOUS)).status_code == 200
    assert (await client.post(ANONYMOUS)).status_code == 429


def test_tampered_anonymous_sessions_are_not_trusted() -> None:
    def user(data: dict[str, object]) -> object:
        return session_user(Request({"type": "http", "session": {"user": data}}))

    # Una sesión anónima nunca administra.
    anonymous = user({"id": "anon:1", "anonymous": True, "admin": True})
    assert anonymous is not None
    assert anonymous.is_anonymous is True  # type: ignore[attr-defined]
    assert anonymous.is_admin is False  # type: ignore[attr-defined]
    # Id y marca de anónimo deben coincidir.
    assert user({"id": "anon:1"}) is None
    assert user({"id": "ana", "anonymous": True}) is None
