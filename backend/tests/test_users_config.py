"""Usuarios (modo de acceso y LDAP) en la configuración editable (#42, parte C)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.ldap_auth import ConfigurableLdap, LdapAuthenticator
from app.core.runtime_config import RuntimeConfig, StaticConfig, UsersConfig
from tests.ldap_fixtures import (
    ADMIN_GROUP,
    PASSWORDS,
    PEOPLE,
    SERVICE_DN,
    ldap_settings,
    mock_authenticator,
    users_config,
)

URL = "/api/v1/admin/config/users"
LOCAL_ADMIN = {"username": "admin", "password": "secreto"}
# Conexión con el directorio simulado (ver ldap_fixtures).
DIRECTORY = {
    "auth_mode": "ldap",
    "ldap_url": "ldap://directorio.ejemplo.org",
    "ldap_bind_dn": SERVICE_DN,
    "ldap_bind_password": "clave-servicio",
    "ldap_user_base_dn": PEOPLE,
    "ldap_admin_group_dn": ADMIN_GROUP,
}


def _use_mock_directory(app: FastAPI) -> None:
    """Los autenticadores que cree la aplicación usan el directorio simulado."""
    ldap: ConfigurableLdap = app.state.ldap
    ldap.factory = mock_authenticator


# --- Modelo ---


def test_ldap_mode_requires_base_and_filter() -> None:
    base = users_config(Settings()).model_dump()
    with pytest.raises(ValidationError, match="base de búsqueda"):
        UsersConfig.model_validate({**base, "auth_mode": "ldap", "ldap_user_base_dn": " "})
    with pytest.raises(ValidationError, match=r"\{username\}"):
        UsersConfig.model_validate(
            {**base, "auth_mode": "ldap", "ldap_user_base_dn": PEOPLE, "ldap_user_filter": "(x)"}
        )
    # Sin LDAP no hace falta.
    assert UsersConfig.model_validate({**base, "ldap_user_base_dn": ""}).auth_mode == "none"


def test_ca_certificate_is_only_set_in_the_administration(tmp_path: Path) -> None:
    """El fichero de CHATBOT_LDAP_CA_CERT_FILE no se copia: se lee en cada conexión."""
    ca = tmp_path / "ca.pem"
    ca.write_text("PEM", encoding="utf-8")
    assert users_config(Settings(ldap_ca_cert_file=ca)).ldap_ca_cert is None
    assert UsersConfig.ADMIN_ONLY_FIELDS == ("ldap_ca_cert",)


def test_configurable_ldap_passes_the_ca_file(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("", encoding="utf-8")
    config = RuntimeConfig.from_settings(
        ldap_settings(Settings(), ldap_url="ldaps://ldap.ejemplo.org")
    )
    authenticator = ConfigurableLdap(StaticConfig(config), ca_file=ca).current()
    assert authenticator._get_server().tls.ca_certs_file == str(ca)


def test_authenticator_follows_the_configuration() -> None:
    config = RuntimeConfig.from_settings(ldap_settings(Settings()))
    source = StaticConfig(config)
    ldap = ConfigurableLdap(source)
    first = ldap.current()
    assert isinstance(first, LdapAuthenticator)
    assert ldap.current() is first
    source._config = config.replace_group(
        "users", config.users.model_copy(update={"ldap_timeout_seconds": 3.0})
    )
    assert ldap.current() is not first


def test_check_connection_with_the_mock_directory() -> None:
    config = users_config(ldap_settings(Settings()))
    assert "base de usuarios encontrada" in mock_authenticator(config).check_connection()
    anonymous = config.model_copy(update={"ldap_bind_dn": None, "ldap_bind_password": None})
    assert "acceso anónimo" in mock_authenticator(anonymous).check_connection()


# --- API, con la cuenta local de administración ---


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def admin(app: FastAPI, client: AsyncClient) -> AsyncClient:
    _use_mock_directory(app)
    assert (await client.post("/api/v1/admin/session", json=LOCAL_ADMIN)).status_code == 200
    return client


async def test_users_group_hides_the_bind_password(app: FastAPI, admin: AsyncClient) -> None:
    await admin.put(URL, json=DIRECTORY)
    response = await admin.get("/api/v1/admin/config")

    users = response.json()["groups"]["users"]
    assert users["values"]["auth_mode"] == "ldap"
    assert users["values"]["ldap_bind_password"] == {"configured": True, "unreadable": False}
    assert users["secret_fields"] == ["ldap_bind_password"]
    assert "clave-servicio" not in response.text


async def test_enabling_ldap_checks_the_directory_and_applies_at_once(
    app: FastAPI, admin: AsyncClient
) -> None:
    response = await admin.put(URL, json=DIRECTORY)

    assert response.status_code == 200
    assert "Ha cambiado el modo de acceso" in response.json()["warnings"][0]
    assert app.state.config.current.users.auth_mode == "ldap"
    # El chat pide ahora iniciar sesión con el directorio.
    session = (await admin.get("/api/v1/auth/session")).json()
    assert session["mode"] == "ldap"
    login = await admin.post(
        "/api/v1/auth/session", json={"username": "luis", "password": PASSWORDS["luis"]}
    )
    assert login.json()["user"]["id"] == "luis"


async def test_unreachable_directory_is_not_saved(app: FastAPI, admin: AsyncClient) -> None:
    response = await admin.put(URL, json={**DIRECTORY, "ldap_bind_password": "caducada"})

    assert response.status_code == 422
    assert "el directorio no responde" in response.json()["detail"]
    assert app.state.config.current.users.auth_mode == "none"


async def test_unchanged_connection_is_not_checked_again(app: FastAPI, admin: AsyncClient) -> None:
    await admin.put(URL, json=DIRECTORY)

    class Unreachable:
        def __init__(self, _: UsersConfig) -> None:
            raise AssertionError("no debe conectar")

    app.state.ldap.factory = Unreachable
    response = await admin.put(URL, json={"ldap_display_name_attribute": "displayName"})
    assert response.status_code == 200
    assert response.json()["warnings"] == []


async def test_disabling_ldap_keeps_the_local_account(app: FastAPI, admin: AsyncClient) -> None:
    await admin.put(URL, json=DIRECTORY)
    response = await admin.put(URL, json={"auth_mode": "none"})

    assert response.status_code == 200
    assert any("usa la cuenta local" in w for w in response.json()["warnings"])
    assert (await admin.get("/api/v1/auth/session")).json()["mode"] == "none"


async def test_directory_connection_check(admin: AsyncClient) -> None:
    ok = (await admin.post(f"{URL}/test", json=DIRECTORY)).json()
    assert ok["ok"] is True
    assert "base de usuarios encontrada" in ok["detail"]

    ana = await admin.post(
        f"{URL}/test",
        json={**DIRECTORY, "test_username": "ana", "test_password": PASSWORDS["ana"]},
    )
    assert ana.json()["ok"] is True
    assert "Usuario «ana» (Ana López) válido; es administrador" in ana.json()["detail"]
    luis = await admin.post(
        f"{URL}/test",
        json={**DIRECTORY, "test_username": "luis", "test_password": PASSWORDS["luis"]},
    )
    assert "no es administrador" in luis.json()["detail"]
    wrong = await admin.post(
        f"{URL}/test", json={**DIRECTORY, "test_username": "ana", "test_password": "mala"}
    )
    assert wrong.json()["ok"] is False
    assert "contraseña no es válida" in wrong.json()["detail"]
    down = await admin.post(f"{URL}/test", json={**DIRECTORY, "ldap_bind_password": "caducada"})
    assert down.json()["ok"] is False
    assert "No se puede conectar con el directorio" in down.json()["detail"]
    # La prueba no guarda nada.
    assert (await admin.get("/api/v1/admin/config")).json()["groups"]["users"]["values"][
        "auth_mode"
    ] == "none"


async def test_directory_user_search_failure_is_reported(
    app: FastAPI, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.ldap_auth import DirectoryUnavailableError

    def broken(self: LdapAuthenticator, username: str, password: str) -> Any:
        raise DirectoryUnavailableError("búsqueda rota")

    monkeypatch.setattr(LdapAuthenticator, "authenticate", broken)
    response = await admin.post(
        f"{URL}/test", json={**DIRECTORY, "test_username": "ana", "test_password": "x"}
    )
    assert response.json() == {
        "ok": False,
        "detail": "Conectado a ldap://directorio.ejemplo.org "
        f"({SERVICE_DN}); base de usuarios encontrada, pero falló la búsqueda del usuario: "
        "búsqueda rota",
    }


# --- Sin cuenta local: el acceso depende de los administradores del directorio ---


class TestWithoutLocalAccount:
    @pytest.fixture
    def settings(self, settings: Settings) -> Settings:
        return ldap_settings(settings, admin_password=None)

    @pytest.fixture
    async def directory_admin(self, app: FastAPI, client: AsyncClient) -> AsyncClient:
        _use_mock_directory(app)
        response = await client.post(
            "/api/v1/admin/session", json={"username": "ana", "password": PASSWORDS["ana"]}
        )
        assert response.status_code == 200
        return client

    async def test_cannot_lock_out_the_administration(
        self, app: FastAPI, directory_admin: AsyncClient
    ) -> None:
        no_group = await directory_admin.put(URL, json={"ldap_admin_group_dn": None})
        assert no_group.status_code == 409
        assert "Nadie podría entrar en la administración" in no_group.json()["detail"]
        no_ldap = await directory_admin.put(URL, json={"auth_mode": "none"})
        assert no_ldap.status_code == 409
        assert app.state.config.current.users.ldap_admin_group_dn == ADMIN_GROUP
