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
from pathlib import Path

import anyio
from ldap3 import BASE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.runtime_config import ConfigSource, UsersConfig

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
    is_auditor: bool = False


class InvalidCredentialsError(Exception):
    """Usuario inexistente o contraseña incorrecta."""


class DirectoryUnavailableError(Exception):
    """No se puede consultar el directorio (red, TLS, cuenta de servicio…)."""


ConnectionFactory = Callable[[str | None, str | None], Connection]


def build_server(config: UsersConfig, ca_file: Path | None = None) -> Server:
    """Servidor LDAP con TLS verificado si se usa ldaps:// o StartTLS.

    La CA es la de la configuración (PEM) o, si no hay, la del fichero `ca_file`
    (CHATBOT_LDAP_CA_CERT_FILE); sin ninguna, la del almacén del sistema.
    """
    use_tls = config.ldap_url.lower().startswith("ldaps://") or config.ldap_start_tls
    tls = None
    if use_tls:
        pem = (config.ldap_ca_cert or "").strip() or None
        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_data=pem,
            ca_certs_file=None if pem or ca_file is None else str(ca_file),
        )
    return Server(config.ldap_url, tls=tls, connect_timeout=config.ldap_timeout_seconds)


class LdapAuthenticator:
    """Valida credenciales y obtiene los datos del usuario."""

    def __init__(
        self,
        config: UsersConfig,
        server: Server | None = None,
        ca_file: Path | None = None,
        **connection_options: object,
    ):
        self._config = config
        self._ca_file = ca_file
        # Se crea al primer inicio de sesión: un error de configuración TLS (p. ej. falta el
        # certificado de la CA) no debe impedir que arranque la aplicación.
        self._server = server
        # Opciones extra para `Connection` (en los tests: client_strategy=MOCK_SYNC).
        self._connection_options = connection_options

    def _get_server(self) -> Server:
        if self._server is None:
            self._server = build_server(self._config, self._ca_file)
        return self._server

    def _bind(self, user: str | None, password: str | None) -> Connection | None:
        """Conexión autenticada, o `None` si el directorio rechaza las credenciales."""
        connection = Connection(
            self._get_server(),
            user=user,
            password=password,
            # ldap3 lo aplica al socket con struct.pack: debe ser un entero.
            receive_timeout=math.ceil(self._config.ldap_timeout_seconds),
            read_only=True,
            **self._connection_options,
        )
        if self._config.ldap_start_tls:
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
        config = self._config
        try:
            service = self._service()
            try:
                entry = self._find_user(service, username)
                user_connection = self._bind(entry.entry_dn, password)
                if user_connection is None:
                    raise InvalidCredentialsError
                user_connection.unbind()
                user_id = _first(entry, config.ldap_user_id_attribute) or username
                return LdapUser(
                    id=user_id.lower(),
                    display_name=_first(entry, config.ldap_display_name_attribute) or user_id,
                    dn=entry.entry_dn,
                    is_admin=self._in_group(
                        service, config.ldap_admin_group_dn, entry.entry_dn, user_id
                    ),
                    is_auditor=self._in_group(
                        service, config.ldap_auditor_group_dn, entry.entry_dn, user_id
                    ),
                )
            finally:
                service.unbind()
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailableError(f"{type(exc).__name__}: {exc}") from exc

    def _service(self) -> Connection:
        """Conexión con la cuenta de servicio (o anónima)."""
        config = self._config
        password = config.ldap_bind_password
        service = self._bind(
            config.ldap_bind_dn or None, password.get_secret_value() if password else None
        )
        if service is None:
            raise DirectoryUnavailableError("El directorio rechazó la cuenta de servicio")
        return service

    def check_connection(self) -> str:
        """Conecta con la cuenta de servicio y comprueba que existe la base de usuarios.

        Devuelve una descripción del resultado; lanza `DirectoryUnavailableError` si falla.
        """
        config = self._config
        try:
            service = self._service()
            try:
                found = service.search(
                    config.ldap_user_base_dn, "(objectClass=*)", BASE, attributes=[]
                )
                if not found or not service.entries:
                    raise DirectoryUnavailableError(
                        f"No existe la base de usuarios «{config.ldap_user_base_dn}» "
                        "o la cuenta de servicio no puede leerla"
                    )
            finally:
                service.unbind()
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        account = config.ldap_bind_dn or "acceso anónimo"
        return f"Conectado a {config.ldap_url} ({account}); base de usuarios encontrada"

    def _find_user(self, connection: Connection, username: str):  # type: ignore[no-untyped-def]
        config = self._config
        search_filter = config.ldap_user_filter.format(username=escape_filter_chars(username))
        connection.search(
            config.ldap_user_base_dn,
            search_filter,
            SUBTREE,
            attributes=[config.ldap_user_id_attribute, config.ldap_display_name_attribute],
            size_limit=2,
        )
        # Ninguna o varias coincidencias: no se puede identificar al usuario con seguridad.
        if len(connection.entries) != 1:
            raise InvalidCredentialsError
        return connection.entries[0]

    def _in_group(
        self, connection: Connection, group: str | None, user_dn: str, user_id: str
    ) -> bool:
        if not group:
            return False
        dn = escape_filter_chars(user_dn)
        if self._config.ldap_nested_groups:
            search_filter = f"(member:{AD_IN_CHAIN}:={dn})"
        else:
            # groupOfNames, groupOfUniqueNames y posixGroup.
            uid = escape_filter_chars(user_id)
            search_filter = f"(|(member={dn})(uniqueMember={dn})(memberUid={uid}))"
        connection.search(group, search_filter, BASE, attributes=[])
        return len(connection.entries) > 0

    async def authenticate_async(self, username: str, password: str) -> LdapUser:
        return await anyio.to_thread.run_sync(self.authenticate, username, password)

    async def check_connection_async(self) -> str:
        return await anyio.to_thread.run_sync(self.check_connection)


AuthenticatorFactory = Callable[[UsersConfig], LdapAuthenticator]


class ConfigurableLdap:
    """Autenticador que sigue la configuración vigente: si cambia, se crea otro."""

    def __init__(
        self,
        source: ConfigSource,
        ca_file: Path | None = None,
        factory: AuthenticatorFactory | None = None,
    ) -> None:
        self._source = source
        # Sustituible en los tests (directorio simulado).
        self.factory: AuthenticatorFactory = factory or (
            lambda config: LdapAuthenticator(config, ca_file=ca_file)
        )
        self._config: UsersConfig | None = None
        self._authenticator: LdapAuthenticator | None = None

    def current(self) -> LdapAuthenticator:
        config = self._source.current.users
        if self._authenticator is None or config != self._config:
            self._authenticator = self.factory(config)
            self._config = config
        return self._authenticator

    def authenticate(self, username: str, password: str) -> LdapUser:
        return self.current().authenticate(username, password)

    async def authenticate_async(self, username: str, password: str) -> LdapUser:
        return await self.current().authenticate_async(username, password)


def _first(entry, attribute: str) -> str | None:  # type: ignore[no-untyped-def]
    """Primer valor de un atributo de la entrada, si existe."""
    if attribute not in entry.entry_attributes:
        return None
    values = entry[attribute].values
    return str(values[0]) if values else None
