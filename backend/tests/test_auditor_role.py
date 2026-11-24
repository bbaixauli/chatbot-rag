"""Grupo de auditores (#50): quién ve la auditoría."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from starlette.requests import Request

from app.api import admin, auth
from app.core.auth import get_staff
from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig
from tests.ldap_fixtures import AUDITOR_GROUP, PASSWORDS, ldap_settings, mock_authenticator

SESSION = "/api/v1/admin/session"
AUDIT = "/api/v1/admin/audit/summary"
ADMIN_ONLY = "/api/v1/admin/prompt"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    configured = ldap_settings(settings, ldap_auditor_group_dn=AUDITOR_GROUP)
    configured.admin_password = SecretStr("secreto")
    return configured


@pytest.fixture(autouse=True)
def directory(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.ldap = mock_authenticator(settings)
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_SECONDS", 0)
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


async def login(client: AsyncClient, username: str, password: str | None = None) -> Any:
    return await client.post(
        SESSION, json={"username": username, "password": password or PASSWORDS[username]}
    )


async def test_auditor_only_sees_the_audit(client: AsyncClient) -> None:
    response = await login(client, "luis")
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "username": "luis",
        "enabled": True,
        "is_admin": False,
        "is_auditor": True,
    }
    assert (await client.get(AUDIT)).status_code == 200
    assert (await client.get(ADMIN_ONLY)).status_code == 401
    session = (await client.get(SESSION)).json()
    assert (session["is_admin"], session["is_auditor"]) == (False, True)


async def test_with_an_auditor_group_admins_do_not_audit(client: AsyncClient) -> None:
    body = (await login(client, "ana")).json()
    assert (body["is_admin"], body["is_auditor"]) == (True, False)
    assert (await client.get(ADMIN_ONLY)).status_code == 200
    assert (await client.get(AUDIT)).status_code == 401


async def test_with_an_auditor_group_the_local_admin_does_not_audit(client: AsyncClient) -> None:
    body = (await login(client, "admin", "secreto")).json()
    assert (body["is_admin"], body["is_auditor"]) == (True, False)
    assert (await client.get(AUDIT)).status_code == 401


async def test_without_an_auditor_group_admins_audit(app: FastAPI, client: AsyncClient) -> None:
    store = app.state.config
    config: RuntimeConfig = store.current
    store._current = config.replace_group(
        "users", config.users.model_copy(update={"ldap_auditor_group_dn": None})
    )
    body = (await login(client, "ana")).json()
    assert (body["is_admin"], body["is_auditor"]) == (True, True)
    assert (await client.get(AUDIT)).status_code == 200

    # Luis (sin roles) no entra en la administración.
    await client.delete(SESSION)
    assert (await login(client, "luis")).status_code == 403


async def test_people_without_roles_and_anonymous_sessions_do_not_audit(
    app: FastAPI, client: AsyncClient
) -> None:
    assert (await client.get(AUDIT)).status_code == 401
    users = app.state.config.current.users
    tampered = Request(
        {
            "type": "http",
            "session": {
                "user": {"id": "anon:1", "anonymous": True, "admin": True, "auditor": True}
            },
        }
    )
    assert get_staff(tampered, users) is None


async def test_admin_area_is_enabled_with_only_an_auditor_group(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    settings.admin_password = None
    store = app.state.config
    config: RuntimeConfig = store.current
    store._current = config.replace_group(
        "users", config.users.model_copy(update={"ldap_admin_group_dn": None})
    )
    assert (await client.get(SESSION)).json()["enabled"] is True
