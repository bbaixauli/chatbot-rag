"""Autenticación de usuarios contra un directorio LDAP (OpenLDAP, Active Directory…).

Flujo:
1. Con la cuenta de servicio (o de forma anónima) se busca la entrada del usuario.
2. Se comprueba la contraseña haciendo bind con el DN encontrado.
3. Si hay grupo de administración configurado, se comprueba si el usuario pertenece a él.

`ldap3` es síncrono: la autenticación se ejecuta en un hilo para no bloquear la API.
"""

import logging
import math
import ssl
from collections.abc import Callable
from dataclasses import dataclass

import anyio
from ldap3 import BASE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Regla de coincidencia de Active Directory para pertenencia a grupos anidados.
AD_IN_CHAIN = "1.2.840.113556.1.4.1941"


@dataclass(frozen=True, slots=True)
class LdapUser:
    """Usuario autenticado en el directorio."""

    id: str
    display_name: str
    dn: str
    is_admin: bool


class InvalidCredentialsError(Exception):
    """Usuario inexistente o contraseña incorrecta."""


class DirectoryUnavailableError(Exception):
    """No se puede consultar el directorio (red, TLS, cuenta de servicio…)."""


ConnectionFactory = Callable[[str | None, str | None], Connection]


def build_server(settings: Settings) -> Server:
    """Servidor LDAP con TLS verificado si se usa ldaps:// o StartTLS."""
    use_tls = settings.ldap_url.lower().startswith("ldaps://") or settings.ldap_start_tls
    tls = (
        Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=str(settings.ldap_ca_cert_file) if settings.ldap_ca_cert_file else None,
        )
        if use_tls
        else None
    )
    return Server(settings.ldap_url, tls=tls, connect_timeout=settings.ldap_timeout_seconds)


class LdapAuthenticator:
    """Valida credenciales y obtiene los datos del usuario."""

    def __init__(
        self, settings: Settings, server: Server | None = None, **connection_options: object
    ):
        self._settings = settings
        # Se crea al primer inicio de sesión: un error de configuración TLS (p. ej. falta el
        # certificado de la CA) no debe impedir que arranque la aplicación.
        self._server = server
        # Opciones extra para `Connection` (en los tests: client_strategy=MOCK_SYNC).
        self._connection_options = connection_options

    def _get_server(self) -> Server:
        if self._server is None:
            self._server = build_server(self._settings)
        return self._server

    def _bind(self, user: str | None, password: str | None) -> Connection | None:
        """Conexión autenticada, o `None` si el directorio rechaza las credenciales."""
        connection = Connection(
            self._get_server(),
            user=user,
            password=password,
            # ldap3 lo aplica al socket con struct.pack: debe ser un entero.
            receive_timeout=math.ceil(self._settings.ldap_timeout_seconds),
            read_only=True,
            **self._connection_options,
        )
        if self._settings.ldap_start_tls:
            connection.open()
            connection.start_tls()
        if not connection.bind():
            connection.unbind()
            return None
        return connection

    def authenticate(self, username: str, password: str) -> LdapUser:
        """Comprueba las credenciales; lanza `InvalidCredentialsError` si no son válidas."""
        username = username.strip()
        # Una contraseña vacía produce un bind anónimo que el servidor puede aceptar.
        if not username or not password:
            raise InvalidCredentialsError
        settings = self._settings
        bind_password = (
            settings.ldap_bind_password.get_secret_value() if settings.ldap_bind_password else None
        )
        try:
            service = self._bind(settings.ldap_bind_dn, bind_password)
            if service is None:
                raise DirectoryUnavailableError("El directorio rechazó la cuenta de servicio")
            try:
                entry = self._find_user(service, username)
                user_connection = self._bind(entry.entry_dn, password)
                if user_connection is None:
                    raise InvalidCredentialsError
                user_connection.unbind()
                user_id = _first(entry, settings.ldap_user_id_attribute) or username
                return LdapUser(
                    id=user_id.lower(),
                    display_name=_first(entry, settings.ldap_display_name_attribute) or user_id,
                    dn=entry.entry_dn,
                    is_admin=self._is_admin(service, entry.entry_dn, user_id),
                )
            finally:
                service.unbind()
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailableError(f"{type(exc).__name__}: {exc}") from exc

    def _find_user(self, connection: Connection, username: str):  # type: ignore[no-untyped-def]
        settings = self._settings
        search_filter = settings.ldap_user_filter.format(username=escape_filter_chars(username))
        connection.search(
            settings.ldap_user_base_dn,
            search_filter,
            SUBTREE,
            attributes=[settings.ldap_user_id_attribute, settings.ldap_display_name_attribute],
            size_limit=2,
        )
        # Ninguna o varias coincidencias: no se puede identificar al usuario con seguridad.
        if len(connection.entries) != 1:
            raise InvalidCredentialsError
        return connection.entries[0]

    def _is_admin(self, connection: Connection, user_dn: str, user_id: str) -> bool:
        group = self._settings.ldap_admin_group_dn
        if not group:
            return False
        dn = escape_filter_chars(user_dn)
        if self._settings.ldap_nested_groups:
            search_filter = f"(member:{AD_IN_CHAIN}:={dn})"
        else:
            # groupOfNames, groupOfUniqueNames y posixGroup.
            uid = escape_filter_chars(user_id)
            search_filter = f"(|(member={dn})(uniqueMember={dn})(memberUid={uid}))"
        connection.search(group, search_filter, BASE, attributes=[])
        return len(connection.entries) > 0

    async def authenticate_async(self, username: str, password: str) -> LdapUser:
        return await anyio.to_thread.run_sync(self.authenticate, username, password)


def _first(entry, attribute: str) -> str | None:  # type: ignore[no-untyped-def]
    """Primer valor de un atributo de la entrada, si existe."""
    if attribute not in entry.entry_attributes:
        return None
    values = entry[attribute].values
    return str(values[0]) if values else None
