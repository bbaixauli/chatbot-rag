"""Configuración editable desde la administración (#42)."""

import io
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app import config as config_cli
from app.core.config import Settings
from app.core.runtime_config import GROUPS, LlmConfig, RuntimeConfig, StaticConfig
from app.core.secrets import SecretCipher, SecretUnreadableError
from app.db.base import Base
from app.db.models import AppSetting, SystemPromptVersion
from app.db.session import build_engine, build_sessionmaker
from app.main import create_app
from app.services.embeddings import ConfigurableEmbeddings, LocalEmbeddings
from app.services.llm import ConfigurableLLM, FakeLLMClient, OpenAICompatibleClient
from app.services.runtime_config import ConfigStore, load_runtime_config
from tests.conftest import set_config
from tests.factories import MARKDOWN

ADMIN = {"username": "admin", "password": "secreto"}
URL = "/api/v1/admin/config"


class Reader:
    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    assert (await client.post("/api/v1/admin/session", json=ADMIN)).status_code == 200
    return client


async def _store(tmp_path: Path, **overrides: Any) -> tuple[ConfigStore, Any]:
    """Almacén sobre una BD nueva (devuelve también el motor para cerrarlo)."""
    settings = Settings(
        **{
            "environment": "test",
            "data_dir": tmp_path,
            "llm_provider": "fake",
            "embedding_provider": "fake",
            "session_secret": "clave-de-sesion-de-pruebas",
            **overrides,
        }
    )
    engine = build_engine(settings.resolved_database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return ConfigStore(build_sessionmaker(engine), settings), engine


async def _rows(store: ConfigStore) -> dict[str, AppSetting]:
    async with store._sessionmaker() as session:
        return {row.key: row for row in await session.scalars(select(AppSetting))}


# --- Modelos y cifrado ---


def test_groups_map_to_settings_fields() -> None:
    settings = Settings()
    for name, group in GROUPS.items():
        for field in group.model_fields:
            if field in group.ADMIN_ONLY_FIELDS:
                continue
            assert hasattr(settings, group.settings_name(field)), f"{name}.{field}"
    assert LlmConfig.env_name("model") == "CHATBOT_LLM_MODEL"
    assert GROUPS["chat"].env_name("system_prompt_max_length") == (
        "CHATBOT_SYSTEM_PROMPT_MAX_LENGTH"
    )


def test_runtime_config_from_settings() -> None:
    config = RuntimeConfig.from_settings(Settings(llm_model="m", rag_top_k=7))
    assert (config.llm.model, config.rag.top_k) == ("m", 7)
    assert StaticConfig(config).current is config


def test_code_never_reads_editable_values_from_settings() -> None:
    """Los valores editables se leen de la configuración vigente, no de `Settings`."""
    names = {
        group.settings_name(field) for group in GROUPS.values() for field in group.model_fields
    }
    pattern = re.compile(r"settings\.(\w+)")
    app_dir = Path(__file__).parents[1] / "app"
    offenders = [
        f"{path.relative_to(app_dir)}: settings.{name}"
        for path in app_dir.rglob("*.py")
        if path.name != "runtime_config.py"
        for name in pattern.findall(path.read_text(encoding="utf-8"))
        if name in names
    ]
    assert offenders == []


def test_secret_cipher_round_trip_and_wrong_key() -> None:
    cipher = SecretCipher(SecretStr("una-clave-de-sesion"))
    token = cipher.encrypt("sk-123")
    assert "sk-123" not in token
    assert cipher.decrypt(token) == "sk-123"
    with pytest.raises(SecretUnreadableError):
        SecretCipher(SecretStr("otra-clave-distinta")).decrypt(token)
    with pytest.raises(SecretUnreadableError):
        cipher.decrypt("no es un token")


# --- Almacén ---


async def test_first_start_copies_settings_to_the_database(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, llm_model="modelo-env", llm_api_key="sk-env")
    try:
        await store.load(seed=True)
        rows = await _rows(store)
        assert set(rows) == {f"config.{name}" for name in GROUPS}
        assert {row.updated_by for row in rows.values()} == {"env"}
        assert rows["config.llm"].value["model"] == "modelo-env"
        # La clave se guarda cifrada.
        assert "sk-env" not in str(rows["config.llm"].value)
        assert set(rows["config.llm"].value["api_key"]) == {"encrypted"}
        assert store.current.llm.api_key == SecretStr("sk-env")
        assert store.info("llm").updated_by == "env"
        assert store.info("llm").updated_at is not None
    finally:
        await engine.dispose()


async def test_saved_values_win_over_environment(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, engine = await _store(tmp_path)
    await store.load(seed=True)
    await store.save("llm", store.current.llm.model_copy(update={"model": "elegido"}), "ana")
    await engine.dispose()

    # Nuevo arranque con otra variable: se ignora y se avisa.
    restarted, engine = await _store(tmp_path, llm_model="del-entorno")
    try:
        with caplog.at_level(logging.WARNING):
            await restarted.load(seed=True)
        assert restarted.current.llm.model == "elegido"
        assert restarted.info("llm").updated_by == "ana"
        assert "CHATBOT_LLM_MODEL se ignora" in caplog.text
    finally:
        await engine.dispose()


async def test_missing_unknown_and_invalid_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, engine = await _store(tmp_path, rag_top_k=4, chat_max_message_length=900)
    try:
        async with store._sessionmaker() as session:
            # Grupo guardado por una versión anterior (le falta `top_k`) con un campo retirado.
            session.add(
                AppSetting(
                    key="config.rag", value={"min_score": 0.5, "retirado": 1}, updated_by="x"
                )
            )
            # Grupo con un valor no válido: se usan las variables de entorno.
            session.add(
                AppSetting(key="config.chat", value={"max_message_length": -1}, updated_by="x")
            )
            await session.commit()
        with caplog.at_level(logging.ERROR):
            await store.load(seed=True)
        assert (store.current.rag.min_score, store.current.rag.top_k) == (0.5, 4)
        assert store.current.chat.max_message_length == 900
        assert "«chat» guardada no es válida" in caplog.text
    finally:
        await engine.dispose()


async def test_unreadable_secret_falls_back_to_environment(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, llm_api_key="sk-original")
    await store.load(seed=True)
    await engine.dispose()

    # Otra clave de sesión: el secreto guardado no se puede descifrar.
    other, engine = await _store(
        tmp_path,
        llm_api_key="sk-del-entorno",
        session_secret="otra-clave-de-sesion-x",  # noqa: S106
    )
    try:
        await other.load(seed=True)
        assert other.unreadable_secrets == {"llm.api_key"}
        assert other.current.llm.api_key == SecretStr("sk-del-entorno")
        # Al guardar el grupo se vuelve a cifrar con la clave actual.
        await other.save("llm", other.current.llm, "ana")
        assert other.unreadable_secrets == frozenset()
        await other.load(seed=False)
        assert other.unreadable_secrets == frozenset()
    finally:
        await engine.dispose()


async def test_reload_and_reset(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, rag_top_k=3)
    try:
        await store.load(seed=True)
        other = ConfigStore(store._sessionmaker, store._settings)
        await other.load(seed=False)
        await store.save("rag", store.current.rag.model_copy(update={"top_k": 9}), "ana")
        assert other.current.rag.top_k == 3
        await other.reload()
        assert other.current.rag.top_k == 9

        await store.reset(["rag"])
        assert store.current.rag.top_k == 3
        assert store.info("rag").updated_by == "env"
    finally:
        await engine.dispose()


async def test_default_prompt_variable_becomes_first_version(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, default_system_prompt="Eres el asistente de RR. HH.")
    try:
        await store.load(seed=True)
        await store.load(seed=True)  # solo la primera vez
        async with store._sessionmaker() as session:
            versions = list(await session.scalars(select(SystemPromptVersion)))
        assert [(v.content, v.created_by) for v in versions] == [
            ("Eres el asistente de RR. HH.", "env")
        ]
    finally:
        await engine.dispose()


async def test_command_line_tools_read_the_database(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, chunk_size_chars=1000)
    await store.load(seed=True)
    await store.save(
        "indexing", store.current.indexing.model_copy(update={"chunk_size_chars": 700}), "ana"
    )
    await engine.dispose()

    assert (await load_runtime_config(store._settings)).indexing.chunk_size_chars == 700
    # Sin BD (o sin migrar) se usan las variables de entorno.
    missing = tmp_path / "no-existe"
    empty = Settings(
        data_dir=missing,
        database_url=f"sqlite+aiosqlite:///{(missing / 'app.db').as_posix()}",
        chunk_size_chars=1000,
    )
    assert (await load_runtime_config(empty)).indexing.chunk_size_chars == 1000


async def test_config_cli_show_and_reset(tmp_path: Path) -> None:
    store, engine = await _store(tmp_path, rag_top_k=3, llm_api_key="sk-1")
    await store.load(seed=True)
    await store.save("rag", store.current.rag.model_copy(update={"top_k": 9}), "ana")
    await engine.dispose()

    parser = config_cli.build_parser()
    out = io.StringIO()
    assert await config_cli.run(parser.parse_args(["show"]), store._settings, out) == 0
    text = out.getvalue()
    assert "[rag] guardado por ana" in text
    assert "top_k = 9" in text
    assert "api_key = (configurada)" in text
    assert "sk-1" not in text

    out = io.StringIO()
    args = parser.parse_args(["reset", "--group", "rag"])
    assert await config_cli.run(args, store._settings, out) == 0
    assert "Restablecido desde CHATBOT_*: rag" in out.getvalue()
    assert "top_k = 3" in out.getvalue()


# --- Clientes que siguen la configuración ---


async def test_llm_client_follows_the_configuration() -> None:
    config = RuntimeConfig.from_settings(Settings(llm_provider="fake"))
    source = StaticConfig(config)
    llm = ConfigurableLLM(source)
    first = llm._current()
    assert isinstance(first, FakeLLMClient)
    assert llm._current() is first
    source._config = config.replace_group(
        "llm", config.llm.model_copy(update={"provider": "openai"})
    )
    assert isinstance(llm._current(), OpenAICompatibleClient)
    assert "".join([part async for part in ConfigurableLLM(StaticConfig(config)).stream_chat([])])


def test_embeddings_follow_the_configuration() -> None:
    config = RuntimeConfig.from_settings(Settings(embedding_provider="local"))
    source = StaticConfig(config)
    embeddings = ConfigurableEmbeddings(source)
    local = embeddings._current()
    assert isinstance(local, LocalEmbeddings)
    # Cambiar el modelo de lenguaje no recarga un modelo local (es costoso).
    source._config = config.replace_group("llm", config.llm.model_copy(update={"temperature": 1.0}))
    assert embeddings._current() is local
    source._config = config.replace_group(
        "embeddings", config.embeddings.model_copy(update={"model": "otro"})
    )
    assert embeddings.model_name == "otro"


# --- API ---


async def test_config_requires_admin(client: AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.put(f"{URL}/rag", json={"top_k": 2})).status_code == 401
    assert (await client.post(f"{URL}/llm/test", json={})).status_code == 401


async def test_get_config_hides_secrets(app: FastAPI, admin: AsyncClient) -> None:
    await set_config(app, "llm", api_key=SecretStr("sk-secreto"))
    response = await admin.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert "sk-secreto" not in response.text
    assert set(body["groups"]) == set(GROUPS)
    llm = body["groups"]["llm"]
    assert llm["values"]["api_key"] == {"configured": True, "unreadable": False}
    assert llm["values"]["provider"] == "fake"
    assert llm["secret_fields"] == ["api_key"]
    assert body["groups"]["embeddings"]["values"]["api_key"] == {
        "configured": False,
        "unreadable": False,
    }
    assert body["groups"]["embeddings"]["reindex_fields"] == ["provider", "model", "base_url"]
    assert body["groups"]["rag"]["updated_by"] == "env"
    assert body["providers"] == {
        "llm": ["openai", "fake"],
        "embeddings": ["openai", "local", "fake"],
    }


async def test_update_applies_immediately(app: FastAPI, admin: AsyncClient) -> None:
    response = await admin.put(f"{URL}/chat", json={"max_message_length": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["values"]["max_message_length"] == 5
    assert body["config"]["updated_by"] == "admin"
    assert body["requires_reindex"] is False
    assert app.state.config.current.chat.max_message_length == 5
    chat = await admin.post("/api/v1/chat", json={"message": "123456"})
    assert chat.status_code == 422


async def test_update_validation(admin: AsyncClient) -> None:
    unknown = await admin.put(f"{URL}/rag", json={"top_k": 3, "otro": 1})
    assert unknown.status_code == 422
    assert "otro" in unknown.json()["detail"]
    invalid = await admin.put(f"{URL}/rag", json={"top_k": 0})
    assert invalid.status_code == 422
    assert "top_k" in invalid.json()["detail"]
    assert (await admin.put(f"{URL}/nada", json={})).status_code == 404
    bad_clear = await admin.put(f"{URL}/llm", json={"clear_secrets": ["model"]})
    assert bad_clear.status_code == 422
    bad_secret = await admin.put(f"{URL}/llm", json={"api_key": 3})
    assert bad_secret.status_code == 422
    # El nombre del modelo se guarda en columnas de 255 caracteres.
    long_model = await admin.put(f"{URL}/llm", json={"model": "m" * 256})
    assert long_model.status_code == 422
    assert "model" in long_model.json()["detail"]


async def test_secrets_are_kept_replaced_or_cleared(app: FastAPI, admin: AsyncClient) -> None:
    await admin.put(f"{URL}/llm", json={"api_key": "sk-1"})
    assert app.state.config.current.llm.api_key == SecretStr("sk-1")
    # Vacío o ausente: se conserva.
    await admin.put(f"{URL}/llm", json={"api_key": "", "model": "otro"})
    assert app.state.config.current.llm.api_key == SecretStr("sk-1")
    assert app.state.config.current.llm.model == "otro"
    response = await admin.put(f"{URL}/llm", json={"clear_secrets": ["api_key"]})
    assert response.json()["config"]["values"]["api_key"]["configured"] is False
    assert app.state.config.current.llm.api_key is None


async def test_reindex_warning_and_blocking(app: FastAPI, admin: AsyncClient) -> None:
    # Sin documentos no hace falta reindexar nada.
    empty = await admin.put(f"{URL}/indexing", json={"chunk_size_chars": 1500})
    assert empty.json()["requires_reindex"] is True
    assert empty.json()["warnings"] == []

    await app.state.documents.upload("guia.md", Reader(MARKDOWN.encode()), "admin")
    # Mientras se indexa no se puede cambiar el troceado ni el modelo.
    busy = await admin.put(f"{URL}/embeddings", json={"model": "otro"})
    assert busy.status_code == 409
    assert "pendientes de indexar" in busy.json()["detail"]
    await app.state.tasks.drain()

    response = await admin.put(f"{URL}/indexing", json={"chunk_size_chars": 800})
    assert response.json()["requires_reindex"] is True
    assert "Reindexar todo" in response.json()["warnings"][0]
    stats = (await admin.get("/api/v1/admin/documents/stats")).json()
    assert stats["outdated"] == 1
    # El tamaño de subida no afecta al índice.
    upload = await admin.put(f"{URL}/indexing", json={"upload_max_mb": 10})
    assert upload.json()["requires_reindex"] is False

    await admin.post("/api/v1/admin/documents/reindex")
    await app.state.tasks.drain()
    assert (await admin.get("/api/v1/admin/documents/stats")).json()["outdated"] == 0


async def test_connection_checks(admin: AsyncClient) -> None:
    llm = await admin.post(f"{URL}/llm/test", json={})
    assert llm.json()["ok"] is True
    assert "responde" in llm.json()["detail"]
    embeddings = await admin.post(f"{URL}/embeddings/test", json={})
    assert embeddings.json() == {
        "ok": True,
        "detail": "El modelo «fake-hashing» devuelve vectores de 256 dimensiones",
    }
    # Se prueban los valores enviados, sin guardarlos.
    unreachable = await admin.post(
        f"{URL}/llm/test",
        json={
            "provider": "openai",
            "base_url": "http://127.0.0.1:9/v1",
            "max_retries": 0,
            "timeout_seconds": 5,
        },
    )
    assert unreachable.json()["ok"] is False
    assert "No se pudo conectar" in unreachable.json()["detail"]
    assert (await admin.get(URL)).json()["groups"]["llm"]["values"]["provider"] == "fake"
    assert (await admin.post(f"{URL}/rag/test", json={})).status_code == 404


async def test_fake_providers_are_not_offered_in_production(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        data_dir=tmp_path / "data",
        llm_provider="fake",
        embedding_provider="fake",
        admin_password="secreto",  # noqa: S106
        session_secret="clave-de-sesion-de-produccion",  # noqa: S106
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await app.state.config.load(seed=True)
        async with _client(app) as client:
            await client.post("/api/v1/admin/session", json=ADMIN)
            providers = (await client.get(URL)).json()["providers"]
            assert providers == {"llm": ["openai"], "embeddings": ["openai", "local"]}
            response = await client.put(f"{URL}/embeddings", json={"provider": "fake"})
            assert response.status_code == 422
            assert "fuera de producción" in response.json()["detail"]


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_app_starts_without_migrated_database(tmp_path: Path) -> None:
    """Sin tablas (falta `alembic upgrade head`) arranca con las variables de entorno."""
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, rag_top_k=6))
    async with app.router.lifespan_context(app):
        assert app.state.config.current.rag.top_k == 6
