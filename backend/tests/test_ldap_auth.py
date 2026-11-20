import ssl
from typing import Any, ClassVar

import pytest
from ldap3 import Connection
from ldap3.core.exceptions import LDAPSocketOpenError
from pydantic import ValidationError

from app.core import ldap_auth
from app.core.config import Settings
from app.core.ldap_auth import (
    AD_IN_CHAIN,
    DirectoryUnavailableError,
    InvalidCredentialsError,
    LdapAuthenticator,
    LdapUser,
    build_server,
)
from tests.ldap_fixtures import (
    ADMIN_GROUP,
    ANA_DN,
    PASSWORDS,
    ldap_settings,
    mock_authenticator,
    users_config,
)


@pytest.fixture
def ldap(settings: Settings) -> Settings:
    return ldap_settings(settings)


def test_admin_user_by_group_membership(ldap: Settings) -> None:
    user = mock_authenticator(ldap).authenticate("ana", PASSWORDS["ana"])

    # El id se normaliza a minúsculas a partir del uid del directorio.
    assert user == LdapUser(id="ana", display_name="Ana López", dn=ANA_DN, is_admin=True)


def test_regular_user_and_member_uid_groups(ldap: Settings) -> None:
    authenticator = mock_authenticator(ldap)

    luis = authenticator.authenticate("  luis ", PASSWORDS["luis"])
    pepe = authenticator.authenticate("pepe", PASSWORDS["pepe"])

    assert (luis.id, luis.display_name, luis.is_admin) == ("luis", "Luis Pérez", False)
    # posixGroup (memberUid) y, sin nombre visible, se usa el id.
    assert (pepe.display_name, pepe.is_admin) == ("pepe", True)


def test_without_admin_group_nobody_is_admin(settings: Settings) -> None:
    authenticator = mock_authenticator(ldap_settings(settings, ldap_admin_group_dn=None))
    assert authenticator.authenticate("ana", PASSWORDS["ana"]).is_admin is False


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("ana", "mala"),
        ("nadie", "x"),
        ("ana", ""),  # un bind sin contraseña sería anónimo
        ("", "x"),
        ("dup", "x"),  # ambiguo
        ("*", PASSWORDS["ana"]),  # comodines escapados
        ("ana)(uid=*", PASSWORDS["ana"]),  # inyección en el filtro
    ],
)
def test_invalid_credentials(ldap: Settings, username: str, password: str) -> None:
    with pytest.raises(InvalidCredentialsError):
        mock_authenticator(ldap).authenticate(username, password)


def test_rejected_service_account_means_directory_unavailable(settings: Settings) -> None:
    from pydantic import SecretStr

    broken = ldap_settings(settings, ldap_bind_password=SecretStr("caducada"))
    with pytest.raises(DirectoryUnavailableError, match="cuenta de servicio"):
        mock_authenticator(broken).authenticate("ana", PASSWORDS["ana"])


def test_socket_errors_mean_directory_unavailable(
    ldap: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any, **__: Any) -> bool:
        raise ConnectionResetError("conexión reiniciada")

    monkeypatch.setattr(Connection, "search", fail)
    with pytest.raises(DirectoryUnavailableError, match="ConnectionResetError"):
        mock_authenticator(ldap).authenticate("ana", PASSWORDS["ana"])


def test_network_errors_mean_directory_unavailable(
    ldap: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any, **__: Any) -> bool:
        raise LDAPSocketOpenError("sin conexión")

    monkeypatch.setattr(Connection, "search", fail)
    with pytest.raises(DirectoryUnavailableError, match="LDAPSocketOpenError"):
        mock_authenticator(ldap).authenticate("ana", PASSWORDS["ana"])


class RecordingConnection:
    """Sustituye a ldap3.Connection para comprobar StartTLS y los filtros enviados."""

    instances: ClassVar[list["RecordingConnection"]] = []

    def __init__(
        self, server: Any, user: str | None = None, password: str | None = None, **options: Any
    ):
        self.user = user
        self.options = options
        self.calls: list[str] = []
        self.searches: list[tuple[str, str]] = []
        self.entries: list[Any] = []
        RecordingConnection.instances.append(self)

    def open(self) -> None:
        self.calls.append("open")

    def start_tls(self) -> None:
        self.calls.append("start_tls")

    def bind(self) -> bool:
        self.calls.append("bind")
        return True

    def unbind(self) -> None:
        self.calls.append("unbind")

    def search(self, base: str, search_filter: str, *_: Any, **__: Any) -> bool:
        self.searches.append((base, search_filter))
        self.entries = [FakeEntry()] if base != ADMIN_GROUP else []
        return True


class FakeEntry:
    entry_dn = "CN=Ana López,OU=Usuarios,DC=empresa,DC=local"
    entry_attributes: ClassVar[list[str]] = ["sAMAccountName"]

    def __getitem__(self, _: str) -> Any:
        return type("Attr", (), {"values": ["ALOPEZ"]})()


def test_start_tls_and_active_directory_nested_groups(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    RecordingConnection.instances = []
    monkeypatch.setattr(ldap_auth, "Connection", RecordingConnection)
    ad = ldap_settings(
        settings,
        ldap_start_tls=True,
        ldap_nested_groups=True,
        ldap_user_filter="(sAMAccountName={username})",
        ldap_user_id_attribute="sAMAccountName",
        ldap_display_name_attribute="displayName",
    )

    user = LdapAuthenticator(users_config(ad)).authenticate("alopez", "clave")

    # Sin nombre visible se muestra el identificador tal como está en el directorio.
    assert (user.id, user.display_name, user.is_admin) == ("alopez", "ALOPEZ", False)
    service, user_connection = RecordingConnection.instances
    assert service.calls == ["open", "start_tls", "bind", "unbind"]
    assert user_connection.calls[:3] == ["open", "start_tls", "bind"]
    assert service.searches[0][1] == "(sAMAccountName=alopez)"
    # ldap3 exige un entero (lo aplica al socket con struct.pack).
    assert service.options["receive_timeout"] == 10
    assert isinstance(service.options["receive_timeout"], int)
    group_base, group_filter = service.searches[1]
    assert group_base == ADMIN_GROUP
    assert group_filter.startswith(f"(member:{AD_IN_CHAIN}:=CN=Ana López")


def test_build_server_verifies_tls(settings: Settings, tmp_path: Any) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("", encoding="utf-8")
    secure_config = users_config(ldap_settings(settings, ldap_url="ldaps://dc.empresa.local:636"))
    secure = build_server(secure_config, ca)
    plain = build_server(users_config(ldap_settings(settings)), ca)

    assert secure.ssl is True
    assert secure.tls.validate == ssl.CERT_REQUIRED
    assert secure.tls.ca_certs_file == str(ca)
    assert plain.ssl is False
    assert plain.tls is None
    # El certificado pegado en la administración tiene prioridad sobre el fichero.
    pasted = build_server(secure_config.model_copy(update={"ldap_ca_cert": "PEM"}), ca)
    assert (pasted.tls.ca_certs_data, pasted.tls.ca_certs_file) == ("PEM", None)


async def test_authenticate_async_runs_in_a_thread(ldap: Settings) -> None:
    user = await mock_authenticator(ldap).authenticate_async("luis", PASSWORDS["luis"])
    assert user.id == "luis"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ldap_user_base_dn": ""}, "LDAP_USER_BASE_DN"),
        ({"ldap_user_filter": "(uid=fijo)"}, "{username}"),
    ],
)
def test_ldap_mode_requires_valid_settings(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**{"auth_mode": "ldap", "ldap_user_base_dn": "ou=people,dc=x", **overrides})


async def test_tls_misconfiguration_does_not_prevent_startup(
    settings: Settings, tmp_path: Any
) -> None:
    from app.main import create_app

    ca = tmp_path / "falta.pem"
    broken = ldap_settings(settings, ldap_url="ldaps://ldap.ejemplo.org", ldap_ca_cert_file=ca)
    app = create_app(broken)
    async with app.router.lifespan_context(app):  # no falla al arrancar
        with pytest.raises(DirectoryUnavailableError, match="LDAPSSLConfigurationError"):
            app.state.ldap.authenticate("ana", "x")

        # El fichero se lee en cada intento (p. ej. tras instalar el certificado).
        ca.write_text("no es un certificado", encoding="utf-8")
        with pytest.raises(DirectoryUnavailableError):
            app.state.ldap.authenticate("ana", "x")

    # Un certificado pegado en la administración que no es válido tampoco impide arrancar.
    bad = users_config(broken).model_copy(update={"ldap_ca_cert": "no es un certificado"})
    with pytest.raises(DirectoryUnavailableError):
        LdapAuthenticator(bad).authenticate("ana", "x")
