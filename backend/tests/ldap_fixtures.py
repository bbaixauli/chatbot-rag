"""Directorio LDAP simulado (ldap3 MOCK_SYNC) para los tests."""

from typing import Any

from ldap3 import MOCK_SYNC, OFFLINE_SLAPD_2_4, Connection, Server
from pydantic import SecretStr

from app.core.config import Settings
from app.core.ldap_auth import LdapAuthenticator
from app.core.runtime_config import UsersConfig

BASE = "dc=ejemplo,dc=org"
PEOPLE = f"ou=people,{BASE}"
SERVICE_DN = f"cn=chatbot,ou=services,{BASE}"
ADMIN_GROUP = f"cn=chatbot-admins,ou=groups,{BASE}"
ANA_DN = f"uid=Ana,{PEOPLE}"
LUIS_DN = f"uid=luis,{PEOPLE}"
PEPE_DN = f"uid=pepe,{PEOPLE}"

# usuario → contraseña
PASSWORDS = {"ana": "clave-ana", "luis": "clave-luis", "pepe": "clave-pepe"}


def ldap_settings(settings: Settings, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "auth_mode": "ldap",
        "ldap_url": "ldap://directorio.ejemplo.org",
        "ldap_bind_dn": SERVICE_DN,
        "ldap_bind_password": SecretStr("clave-servicio"),
        "ldap_user_base_dn": PEOPLE,
        "ldap_admin_group_dn": ADMIN_GROUP,
        **overrides,
    }
    return settings.model_copy(update=values)


def build_directory() -> Server:
    """Servidor simulado con una cuenta de servicio, cuatro usuarios y un grupo."""
    server = Server("directorio-simulado", get_info=OFFLINE_SLAPD_2_4)
    connection = Connection(server, client_strategy=MOCK_SYNC)
    add = connection.strategy.add_entry
    add(PEOPLE, {"objectClass": "organizationalUnit", "ou": "people"})
    add(
        SERVICE_DN,
        {"objectClass": "person", "cn": "chatbot", "sn": "s", "userPassword": "clave-servicio"},
    )
    # El uid se guarda con mayúsculas para comprobar que el id se normaliza.
    add(ANA_DN, _person("Ana", "Ana López", PASSWORDS["ana"]))
    add(LUIS_DN, _person("luis", "Luis Pérez", PASSWORDS["luis"]))
    # Miembro del grupo por memberUid (posixGroup), sin nombre visible.
    add(
        PEPE_DN,
        {
            "objectClass": "inetOrgPerson",
            "uid": "pepe",
            "sn": "p",
            "cn": "",
            "userPassword": PASSWORDS["pepe"],
        },
    )
    # Dos entradas con el mismo uid: no se puede saber quién es.
    add(f"uid=dup,ou=a,{PEOPLE}", _person("dup", "Duplicado A", "x"))
    add(f"uid=dup,ou=b,{PEOPLE}", _person("dup", "Duplicado B", "x"))
    add(
        ADMIN_GROUP,
        {
            "objectClass": "groupOfNames",
            "cn": "chatbot-admins",
            "member": [ANA_DN],
            "memberUid": ["pepe"],
        },
    )
    return server


def _person(uid: str, name: str, password: str) -> dict[str, Any]:
    return {
        "objectClass": "inetOrgPerson",
        "uid": uid,
        "cn": name,
        "sn": "x",
        "userPassword": password,
    }


def users_config(config: Settings | UsersConfig) -> UsersConfig:
    """Configuración de usuarios a partir de `Settings` (valores iniciales) o tal cual."""
    if isinstance(config, UsersConfig):
        return config
    return UsersConfig.model_validate(UsersConfig.values_from_settings(config))


def mock_authenticator(
    config: Settings | UsersConfig, server: Server | None = None
) -> LdapAuthenticator:
    return LdapAuthenticator(
        users_config(config), server or build_directory(), client_strategy=MOCK_SYNC
    )
