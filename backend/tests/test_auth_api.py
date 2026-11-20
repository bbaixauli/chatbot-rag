from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.api import admin, auth
from app.core.config import Settings
from app.core.ldap_auth import DirectoryUnavailableError, LdapUser
from tests.conftest import parse_sse, set_config
from tests.ldap_fixtures import PASSWORDS, ldap_settings, mock_authenticator

URL = "/api/v1/auth/session"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return ldap_settings(settings)


@pytest.fixture(autouse=True)
def directory(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.ldap = mock_authenticator(settings)
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_SECONDS", 0)
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


async def login(client: AsyncClient, username: str) -> dict[str, Any]:
    response = await client.post(URL, json={"username": username, "password": PASSWORDS[username]})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/conversations"),
        ("GET", "/api/v1/conversations/x/messages"),
        ("POST", "/api/v1/chat"),
        ("GET", "/api/v1/chat/config"),
        ("GET", "/api/v1/documents/x/file"),
    ],
)
async def test_chat_requires_login(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, json={"message": "hola"})
    assert response.status_code == 401


async def test_session_lifecycle(client: AsyncClient) -> None:
    assert (await client.get(URL)).json() == {
        "mode": "ldap",
        "authenticated": False,
        "user": None,
        "anonymous_allowed": False,
        "private_sessions": False,
    }

    body = await login(client, "ana")

    expected = {
        "mode": "ldap",
        "authenticated": True,
        "user": {"id": "ana", "display_name": "Ana López", "is_admin": True, "is_anonymous": False},
        "anonymous_allowed": False,
        "private_sessions": False,
    }
    assert body == expected
    assert (await client.get(URL)).json() == expected
    assert (await client.get("/api/v1/conversations")).status_code == 200

    assert (await client.delete(URL)).status_code == 204
    assert (await client.get(URL)).json()["authenticated"] is False
    assert (await client.get("/api/v1/conversations")).status_code == 401


async def test_conversations_are_private_to_each_user(client: AsyncClient) -> None:
    await login(client, "ana")
    chat = await client.post("/api/v1/chat", json={"message": "Pregunta de Ana"})
    conversation_id = parse_sse(chat.text)[0][1]["id"]
    titles = [c["title"] for c in (await client.get("/api/v1/conversations")).json()]
    assert titles == ["Pregunta de Ana"]

    await login(client, "luis")

    assert (await client.get("/api/v1/conversations")).json() == []
    other = f"/api/v1/conversations/{conversation_id}"
    assert (await client.get(f"{other}/messages")).status_code == 404
    assert (await client.patch(other, json={"title": "robada"})).status_code == 404
    assert (await client.delete(other)).status_code == 404
    hijack = await client.post(
        "/api/v1/chat", json={"message": "hola", "conversation_id": conversation_id}
    )
    assert hijack.status_code == 404

    await login(client, "ana")
    assert len((await client.get(f"{other}/messages")).json()) == 2


async def test_wrong_credentials_and_directory_errors(app: FastAPI, client: AsyncClient) -> None:
    bad = await client.post(URL, json={"username": "ana", "password": "mala"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Usuario o contraseña incorrectos"

    class Down:
        async def authenticate_async(self, username: str, password: str) -> LdapUser:
            raise DirectoryUnavailableError("timeout")

    app.state.ldap = Down()
    down = await client.post(URL, json={"username": "ana", "password": "x"})
    assert down.status_code == 503
    assert "Inténtalo más tarde" in down.json()["detail"]


async def test_login_replaces_previous_session(client: AsyncClient) -> None:
    await login(client, "ana")
    assert (await client.get("/api/v1/admin/session")).json()["authenticated"] is True

    await login(client, "luis")

    assert (await client.get("/api/v1/admin/session")).json()["authenticated"] is False


async def test_directory_admins_can_administer(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/session", json={"username": "ana", "password": PASSWORDS["ana"]}
    )
    assert response.json() == {"authenticated": True, "username": "ana", "enabled": True}

    saved = await client.put("/api/v1/admin/prompt", json={"content": "Prompt de Ana"})
    assert saved.status_code == 200
    versions = (await client.get("/api/v1/admin/prompt/versions")).json()
    assert versions[0]["created_by"] == "ana"
    # La misma sesión sirve para el chat.
    assert (await client.get(URL)).json()["user"]["id"] == "ana"


async def test_regular_users_cannot_administer(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/session", json={"username": "luis", "password": PASSWORDS["luis"]}
    )

    assert response.status_code == 403
    assert "permisos de administración" in response.json()["detail"]
    assert (await client.get("/api/v1/admin/prompt")).status_code == 401
    assert (await client.get("/api/v1/conversations")).status_code == 200


async def test_admin_logout_with_ldap_closes_the_chat_session_too(client: AsyncClient) -> None:
    """Con LDAP, la administración y el chat son la misma sesión del usuario."""
    response = await client.post(
        "/api/v1/admin/session", json={"username": "ana", "password": PASSWORDS["ana"]}
    )
    assert response.status_code == 200

    assert (await client.delete("/api/v1/admin/session")).status_code == 204

    assert (await client.get("/api/v1/admin/prompt")).status_code == 401
    assert (await client.get("/api/v1/conversations")).status_code == 401


async def test_local_admin_account_still_works_without_asking_the_directory(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.settings.admin_password = SecretStr("emergencia")

    class MustNotBeCalled:
        async def authenticate_async(self, username: str, password: str) -> LdapUser:
            raise AssertionError("la cuenta local no debe enviarse al directorio")

    app.state.ldap = MustNotBeCalled()
    bad = await client.post("/api/v1/admin/session", json={"username": "admin", "password": "x"})
    good = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "emergencia"}
    )

    assert bad.status_code == 401
    assert good.json()["username"] == "admin"
    assert (await client.get("/api/v1/admin/prompt")).status_code == 200


async def test_admin_disabled_without_group_or_local_account(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.settings.ldap_admin_group_dn = None

    assert (await client.get("/api/v1/admin/session")).json()["enabled"] is False
    response = await client.post(
        "/api/v1/admin/session", json={"username": "ana", "password": PASSWORDS["ana"]}
    )
    assert response.status_code == 503


async def test_user_login_is_rate_limited(app: FastAPI, client: AsyncClient) -> None:
    await set_config(app, "limits", login_per_minute=1)
    await client.post(URL, json={"username": "ana", "password": "mala"})

    assert (await client.post(URL, json={"username": "ana", "password": "x"})).status_code == 429


async def test_without_ldap_the_chat_stays_anonymous(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings.auth_mode = "none"

    assert (await client.get(URL)).json() == {
        "mode": "none",
        "authenticated": False,
        "user": None,
        "anonymous_allowed": False,
        "private_sessions": False,
    }
    response = await client.post(URL, json={"username": "ana", "password": PASSWORDS["ana"]})
    assert response.status_code == 409
    assert (await client.get("/api/v1/conversations")).status_code == 200


async def test_tampered_session_data_is_ignored(app: FastAPI, client: AsyncClient) -> None:
    """Una sesión con datos inesperados no identifica a nadie (la cookie va firmada)."""
    from starlette.requests import Request

    from app.core.auth import session_user

    request = Request({"type": "http", "session": {"user": {"id": 5}}})
    assert session_user(request) is None
    request = Request({"type": "http", "session": {"user": {"id": "x", "admin": "true"}}})
    user = session_user(request)
    assert user is not None
    assert user.is_admin is False
