"""Certificados de confianza propios y comprobación X.509 estricta (#89).

Se usa un servidor HTTPS real (`tls_server.py`) con una CA propia sin *Authority Key
Identifier*: sin la CA no es de confianza; con ella, la comprobación estricta lo rechaza; con
la CA y sin la estricta, funciona.
"""

import ssl
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig, StaticConfig, TlsConfig, WebConfig
from app.core.tls import describe_certificate_error, parse_certificates, ssl_context
from app.services.embeddings import (
    ConfigurableEmbeddings,
    EmbeddingError,
    OpenAICompatibleEmbeddings,
)
from app.services.llm import ChatMessage, ConfigurableLLM, LLMError, OpenAICompatibleClient
from app.services.web import WebError, WebFetcher
from tests.tls_server import TlsServer, tls_server

CONFIG = "/api/v1/admin/config"
UNTRUSTED = "no es de una CA de confianza"
STRICT = "no supera la comprobación X.509 estricta"
MAX_BYTES = 1024 * 1024


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TlsServer]:
    with tls_server(tmp_path_factory.mktemp("tls")) as running:
        yield running


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


def _tls(server: TlsServer | None = None, strict: bool = True) -> TlsConfig:
    return TlsConfig(ca_certs=server.ca_pem if server else None, strict_x509=strict)


WEB = WebConfig(
    timeout_seconds=10,
    allowed_domains=None,
    refresh_enabled=True,
    refresh_interval_hours=24,
    not_found_limit=3,
)


# --- Certificados y contexto --------------------------------------------------------------


def test_certificates_are_validated(server: TlsServer) -> None:
    # Varios certificados, con texto entre ellos (p. ej. el nombre de cada uno).
    pem = f"CA corporativa\n{server.ca_pem}\nOtra copia\n{server.ca_pem}"
    assert len(parse_certificates(pem)) == 2
    assert TlsConfig(ca_certs=f"  {pem}\n", strict_x509=True).ca_certs == pem.strip()
    assert TlsConfig(ca_certs="  \n", strict_x509=True).ca_certs is None

    with pytest.raises(ValueError, match="No hay ningún certificado en formato PEM"):
        parse_certificates("MIIBszCCAVmgAwIBAgIU")
    broken = "-----BEGIN CERTIFICATE-----\nZm9v\n-----END CERTIFICATE-----\n"
    with pytest.raises(ValidationError, match="Los certificados no son válidos"):
        TlsConfig(ca_certs=broken, strict_x509=True)


def test_the_strict_check_is_set_explicitly(server: TlsServer) -> None:
    strict = ssl_context(None, True)
    assert strict.verify_flags & ssl.VERIFY_X509_STRICT
    assert not ssl_context(server.ca_pem, False).verify_flags & ssl.VERIFY_X509_STRICT
    # Se reutiliza (crearlo carga todas las CA públicas).
    assert ssl_context(None, True) is strict


def test_strict_check_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert RuntimeConfig.from_settings(Settings()).tls == _tls()
    monkeypatch.setenv("CHATBOT_TLS_STRICT_X509", "false")
    assert RuntimeConfig.from_settings(Settings()).tls.strict_x509 is False


def test_other_errors_are_not_certificate_errors() -> None:
    assert describe_certificate_error(ConnectionRefusedError("rechazada"), True) is None
    expired = ssl.SSLCertVerificationError(1, "certificate verify failed")
    expired.verify_message = "certificate has expired"
    assert describe_certificate_error(expired, True) == (
        "El certificado del servidor no está en vigor (certificate has expired)."
    )
    name = ssl.SSLCertVerificationError(1, "certificate verify failed")
    name.verify_message = "Hostname mismatch, certificate is not valid for 'otro'."
    assert "no corresponde a su dirección" in (describe_certificate_error(name, False) or "")
    other = ssl.SSLCertVerificationError(1, "certificate verify failed")
    other.verify_message = "invalid CA certificate"
    assert describe_certificate_error(other, False) == (
        "El certificado del servidor no es válido (invalid CA certificate)."
    )


# --- Documentos web ---------------------------------------------------------------------


async def _fetch(server: TlsServer, tls: TlsConfig) -> str:
    download = await WebFetcher().fetch(f"{server.url}/normas", WEB, MAX_BYTES, tls=tls)
    return download.body.decode()


async def test_web_downloads_use_the_trusted_certificates(server: TlsServer) -> None:
    with pytest.raises(WebError, match=UNTRUSTED) as untrusted:
        await _fetch(server, _tls())
    assert "Administración → Configuración → Certificados" in str(untrusted.value)

    with pytest.raises(WebError, match=STRICT) as strict:
        await _fetch(server, _tls(server))
    assert "Missing Authority Key Identifier" in str(strict.value)

    assert "Normas de la intranet" in await _fetch(server, _tls(server, strict=False))


async def test_adding_a_url_after_configuring_the_certificates(
    app: FastAPI, admin: AsyncClient, server: TlsServer
) -> None:
    async def add() -> dict[str, str | None]:
        response = await admin.post(
            "/api/v1/admin/documents/urls", json={"urls": [f"{server.url}/normas"]}
        )
        assert response.status_code == 200, response.text
        [result] = response.json()
        return {"error": result["error"], "title": (result["document"] or {}).get("title")}

    assert UNTRUSTED in ((await add())["error"] or "")

    saved = await admin.put(f"{CONFIG}/tls", json={"ca_certs": server.ca_pem, "strict_x509": False})
    assert saved.status_code == 200, saved.text
    assert await add() == {"error": None, "title": "Normas de la intranet"}


# --- Modelo de lenguaje y embeddings ------------------------------------------------------


def _config(server: TlsServer, tls: TlsConfig) -> RuntimeConfig:
    config = RuntimeConfig.from_settings(
        Settings(llm_base_url=f"{server.url}/v1", embedding_provider="openai")
    )
    return config.replace_group("tls", tls)


async def test_embeddings_use_the_trusted_certificates(server: TlsServer) -> None:
    with pytest.raises(EmbeddingError, match=UNTRUSTED):
        await OpenAICompatibleEmbeddings(_config(server, _tls())).embed(["hola"])
    with pytest.raises(EmbeddingError, match=STRICT):
        await OpenAICompatibleEmbeddings(_config(server, _tls(server))).embed(["hola"])

    embeddings = OpenAICompatibleEmbeddings(_config(server, _tls(server, strict=False)))
    assert await embeddings.embed(["hola", "adiós"]) == [[1.0, 0.0, 0.0]] * 2


async def test_the_language_model_uses_the_trusted_certificates(server: TlsServer) -> None:
    async def ask(tls: TlsConfig) -> str:
        client = OpenAICompatibleClient(_config(server, tls).llm, tls=tls)
        with pytest.raises(LLMError) as error:
            async for _ in client.stream_chat([ChatMessage(role="user", content="Hola")]):
                pass
        return str(error.value)

    assert UNTRUSTED in await ask(_tls())
    assert STRICT in await ask(_tls(server))
    # Con la CA se conecta: el error es ya del servidor (no tiene chat).
    connected = await ask(_tls(server, strict=False))
    assert "certificado" not in connected
    assert "NotFoundError" in connected


def test_clients_are_recreated_when_the_certificates_change(server: TlsServer) -> None:
    config = _config(server, _tls())
    source = StaticConfig(config)
    llm, embeddings = ConfigurableLLM(source), ConfigurableEmbeddings(source)
    first_llm, first_embeddings = llm._current(), embeddings._current()
    assert llm._current() is first_llm

    source._config = config.replace_group("tls", _tls(server, strict=False))
    assert llm._current() is not first_llm
    assert embeddings._current() is not first_embeddings


# --- Administración -----------------------------------------------------------------------


async def test_certificates_in_the_administration(admin: AsyncClient, server: TlsServer) -> None:
    group = (await admin.get(CONFIG)).json()["groups"]["tls"]
    assert group["values"] == {"ca_certs": None, "strict_x509": True}

    invalid = await admin.put(f"{CONFIG}/tls", json={"ca_certs": "no es un certificado"})
    assert invalid.status_code == 422
    assert "No hay ningún certificado en formato PEM" in invalid.json()["detail"]

    # La prueba de conexión del modelo usa los certificados guardados.
    test = {"provider": "openai", "base_url": f"{server.url}/v1"}
    before = (await admin.post(f"{CONFIG}/llm/test", json=test)).json()
    assert before["ok"] is False
    assert UNTRUSTED in before["detail"]

    await admin.put(f"{CONFIG}/tls", json={"ca_certs": server.ca_pem, "strict_x509": False})
    after = (await admin.post(f"{CONFIG}/llm/test", json=test)).json()
    assert "certificado" not in after["detail"]
