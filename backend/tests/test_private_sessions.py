"""Sesiones privadas y temporales sin LDAP, activables desde la administración (#33)."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app.api import admin
from app.core.auth import ANONYMOUS_SESSION_PREFIX
from app.core.config import Settings
from app.db.models import Conversation
from tests.conftest import parse_sse

SESSION = "/api/v1/auth/session"
ANONYMOUS = "/api/v1/auth/anonymous"
ACCESS = "/api/v1/admin/access"
ADMIN_SESSION = "/api/v1/admin/session"
ADMIN_CREDENTIALS = {"username": "admin", "password": "secreto-admin"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    assert settings.auth_mode == "none"
    settings.admin_password = SecretStr(ADMIN_CREDENTIALS["password"])
    return settings


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


def new_client(app: FastAPI) -> AsyncClient:
    """Otro navegador: su propia cookie de sesión."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def admin_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with new_client(app) as client:
        assert (await client.post(ADMIN_SESSION, json=ADMIN_CREDENTIALS)).status_code == 200
        yield client


async def set_private_sessions(admin_client: AsyncClient, enabled: bool) -> None:
    response = await admin_client.put(ACCESS, json={"private_sessions": enabled})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "mode": "none",
        "anonymous_allowed": False,
        "private_sessions": enabled,
    }


async def start_private_session(client: AsyncClient) -> str:
    response = await client.post(ANONYMOUS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "none"
    assert body["authenticated"] is True
    assert body["private_sessions"] is True
    assert body["user"]["is_anonymous"] is True
    assert body["user"]["id"].startswith(ANONYMOUS_SESSION_PREFIX)
    return str(body["user"]["id"])


async def ask(client: AsyncClient, question: str) -> str:
    response = await client.post("/api/v1/chat", json={"message": question})
    assert response.status_code == 200, response.text
    return str(parse_sse(response.text)[0][1]["id"])


async def titles(client: AsyncClient) -> list[str]:
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200, response.text
    return [c["title"] for c in response.json()]


# --- Desactivado (por defecto): historial compartido, como hasta ahora ---


async def test_shared_history_by_default(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    assert (await admin_client.get(ACCESS)).json() == {
        "mode": "none",
        "anonymous_allowed": False,
        "private_sessions": False,
    }
    assert (await client.get(SESSION)).json()["private_sessions"] is False
    assert (await client.post(ANONYMOUS)).status_code == 403

    await ask(client, "Pregunta compartida")
    async with new_client(app) as other:
        assert await titles(other) == ["Pregunta compartida"]


async def test_only_the_setting_of_the_mode_can_be_changed(admin_client: AsyncClient) -> None:
    response = await admin_client.put(ACCESS, json={"anonymous_allowed": True})
    assert response.status_code == 409
    # Sin cambios: devuelve el estado actual.
    assert (await admin_client.put(ACCESS, json={})).json()["private_sessions"] is False


async def test_only_admins_change_the_setting(client: AsyncClient) -> None:
    assert (await client.put(ACCESS, json={"private_sessions": True})).status_code == 401
    assert (await client.get(ACCESS)).status_code == 401


# --- Activado: cada visitante, su sesión privada ---


async def test_private_session_is_required(client: AsyncClient, admin_client: AsyncClient) -> None:
    await set_private_sessions(admin_client, True)

    session = (await client.get(SESSION)).json()
    assert session == {
        "mode": "none",
        "authenticated": False,
        "user": None,
        "anonymous_allowed": False,
        "private_sessions": True,
    }
    for method, path in [
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/chat"),
        ("GET", "/api/v1/chat/config"),
        ("GET", "/api/v1/documents/x/file"),
    ]:
        response = await client.request(method, path, json={"message": "hola"})
        assert response.status_code == 401, path


async def test_simultaneous_visitors_only_see_their_conversations(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    await ask(client, "Pregunta del historial compartido")
    await set_private_sessions(admin_client, True)

    async with new_client(app) as first, new_client(app) as second:
        first_id = await start_private_session(first)
        second_id = await start_private_session(second)
        assert first_id != second_id
        # El historial compartido anterior no aparece en ninguna sesión privada.
        assert await titles(first) == []

        conversation = await ask(first, "Pregunta de la primera persona")
        await ask(second, "Pregunta de la segunda persona")

        assert await titles(first) == ["Pregunta de la primera persona"]
        assert await titles(second) == ["Pregunta de la segunda persona"]
        other = f"/api/v1/conversations/{conversation}"
        assert (await second.get(f"{other}/messages")).status_code == 404
        assert (await second.patch(other, json={"title": "robada"})).status_code == 404
        assert (await second.delete(other)).status_code == 404
        hijack = await second.post(
            "/api/v1/chat", json={"message": "hola", "conversation_id": conversation}
        )
        assert hijack.status_code == 404


async def test_ending_the_session_starts_an_empty_one(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    await set_private_sessions(admin_client, True)
    first_id = await start_private_session(client)
    await ask(client, "Pregunta de la primera sesión")

    # «Terminar sesión» empieza otra directamente.
    second_id = await start_private_session(client)

    assert second_id != first_id
    assert await titles(client) == []
    # Cerrar la sesión (sin empezar otra) también la termina.
    assert (await client.delete(SESSION)).status_code == 204
    assert (await client.get("/api/v1/conversations")).status_code == 401

    # Las conversaciones se conservan para auditoría.
    async with app.state.sessionmaker() as db:
        owners = set(await db.scalars(select(Conversation.user_id)))
    assert first_id in owners


async def test_disabling_returns_to_the_shared_history(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    await ask(client, "Pregunta compartida")
    await set_private_sessions(admin_client, True)
    await start_private_session(client)
    await ask(client, "Pregunta privada")

    await set_private_sessions(admin_client, False)

    # La sesión privada se descarta y se vuelve al historial compartido, sin las privadas.
    assert await titles(client) == ["Pregunta compartida"]
    assert (await client.get(SESSION)).json()["authenticated"] is False
    await set_private_sessions(admin_client, True)
    assert (await client.get("/api/v1/conversations")).status_code == 401


async def test_admin_and_private_chat_sessions_are_independent(
    app: FastAPI, admin_client: AsyncClient
) -> None:
    """Sin LDAP comparten cookie: entrar o salir de una no cierra la otra."""
    await set_private_sessions(admin_client, True)
    async with new_client(app) as browser:
        await start_private_session(browser)
        await ask(browser, "Pregunta antes de administrar")

        assert (await browser.post(ADMIN_SESSION, json=ADMIN_CREDENTIALS)).status_code == 200
        assert await titles(browser) == ["Pregunta antes de administrar"]

        # Empezar otra sesión privada no cierra la administración.
        await start_private_session(browser)
        assert (await browser.get("/api/v1/admin/prompt")).status_code == 200

        await ask(browser, "Pregunta con la administración abierta")
        assert (await browser.delete(ADMIN_SESSION)).status_code == 204
        assert await titles(browser) == ["Pregunta con la administración abierta"]
        assert (await browser.get("/api/v1/admin/prompt")).status_code == 401

        # Cerrar la sesión del chat tampoco cierra la administración.
        assert (await browser.post(ADMIN_SESSION, json=ADMIN_CREDENTIALS)).status_code == 200
        assert (await browser.delete(SESSION)).status_code == 204
        assert (await browser.get("/api/v1/admin/prompt")).status_code == 200


async def test_private_session_never_administers(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    await set_private_sessions(admin_client, True)
    await start_private_session(client)
    assert (await client.get("/api/v1/admin/prompt")).status_code == 401
    assert (await client.put(ACCESS, json={"private_sessions": False})).status_code == 401


async def test_private_sessions_have_their_own_rate_limit(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    """No comparten el límite de los inicios de sesión: varias personas tras una misma IP
    deben poder entrar."""
    await set_private_sessions(admin_client, True)
    app.state.settings.rate_limit_login_per_minute = 1
    app.state.settings.rate_limit_chat_per_minute = 3

    codes = [(await client.post(ANONYMOUS)).status_code for _ in range(4)]

    assert codes == [200, 200, 200, 429]


async def test_directory_sessions_are_not_private_sessions(
    app: FastAPI, client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Una sesión LDAP de antes de pasar a `none` no sirve como sesión privada."""
    from tests.ldap_fixtures import PASSWORDS, ldap_settings, mock_authenticator

    await set_private_sessions(admin_client, True)
    settings: Settings = app.state.settings
    ldap = ldap_settings(settings)
    app.state.ldap = mock_authenticator(ldap)
    for field in ("auth_mode", "ldap_url", "ldap_user_base_dn", "ldap_admin_group_dn"):
        setattr(settings, field, getattr(ldap, field))
    login = await client.post(SESSION, json={"username": "luis", "password": PASSWORDS["luis"]})
    assert login.status_code == 200

    settings.auth_mode = "none"

    assert (await client.get("/api/v1/conversations")).status_code == 401
    assert (await client.get(SESSION)).json()["authenticated"] is False
