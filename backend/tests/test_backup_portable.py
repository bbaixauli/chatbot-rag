"""Copias de seguridad con PostgreSQL (formato portable, #60)."""

import asyncio
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.backup import (
    BackupError,
    create_portable_backup,
    main,
    restore_portable_backup,
)
from app.core.config import Settings
from app.db import portable
from app.db.base import Base
from app.main import create_app
from tests.factories import MARKDOWN

pytestmark = pytest.mark.postgres_only

CREDENTIALS = {"username": "admin", "password": "secreto"}
# Tablas que la restauración debe dejar exactamente como estaban al copiar.
UNCHANGED = ("conversations", "messages", "message_sources", "question_log", "app_settings")


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def migrated(app: FastAPI, settings: Settings) -> None:
    """La aplicación de los tests crea las tablas sin Alembic: se marcan como migradas, como
    en un despliegue real (las copias guardan la revisión)."""
    config = portable._alembic_config(settings.resolved_database_url)
    await asyncio.to_thread(command.stamp, config, "head")


async def _login(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/admin/session", json=CREDENTIALS)).status_code == 200


async def _chat(client: AsyncClient, message: str) -> None:
    response = await client.post("/api/v1/chat", json={"message": message})
    assert response.status_code == 200
    assert "event: done" in response.text


async def _rows(app: FastAPI, table: str) -> list[dict[str, Any]]:
    model = Base.metadata.tables[table]
    async with app.state.sessionmaker() as session:
        result = await session.execute(sa.select(model).order_by(*model.primary_key.columns))
        return [dict(row._mapping) for row in result]


def _archive(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    path = tmp_path / "manipulada.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _head() -> str:
    config = Config()
    config.set_main_option("script_location", str(portable.BACKEND_DIR / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def _manifest(**changes: Any) -> bytes:
    manifest = {
        "format": portable.FORMAT,
        "version": portable.FORMAT_VERSION,
        "alembic_revision": _head(),
        "tables": {},
        **changes,
    }
    return json.dumps(manifest).encode()


@pytest.mark.usefixtures("migrated")
async def test_round_trip_restores_the_data_and_continues_the_ids(
    app: FastAPI, client: AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    await _login(client)
    response = await client.post(
        "/api/v1/admin/documents", files=[("files", ("guia.md", MARKDOWN.encode()))]
    )
    assert response.status_code == 200
    await app.state.tasks.drain()
    await _chat(client, "¿Cuántos días de vacaciones tengo?")
    before = {table: await _rows(app, table) for table in UNCHANGED}
    assert before["message_sources"], "la respuesta debería citar el documento"

    # Se crea con la aplicación en marcha.
    archive = await create_portable_backup(settings, tmp_path / "copias")

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("portable/manifest.json").read())  # type: ignore[union-attr]
    assert "portable/question_log.jsonl" in names
    assert any(name.startswith("uploads/") and name.endswith(".md") for name in names)
    assert manifest["dialect"] == "postgresql"
    assert manifest["tables"]["conversations"] == 1
    assert manifest["tables"]["documents"] == 1

    # Cambios posteriores a la copia: desaparecen al restaurar.
    await _chat(client, "Posterior a la copia")
    await app.state.engine.dispose()

    assert await restore_portable_backup(settings, archive, force=True) == 1

    for table in UNCHANGED:
        assert await _rows(app, table) == before[table], table
    [document] = await _rows(app, "documents")
    assert (document["status"], document["chunk_count"]) == ("pending", 0)
    assert any(settings.uploads_dir.iterdir())
    # Las secuencias continúan tras los ids restaurados: se pueden guardar filas nuevas.
    await _chat(client, "Después de restaurar")
    assert len(await _rows(app, "messages")) == len(before["messages"]) + 2

    # Al arrancar, la aplicación reindexa los documentos restaurados.
    new_app = create_app(settings)
    async with new_app.router.lifespan_context(new_app):
        await new_app.state.tasks.drain()
        transport = ASGITransport(app=new_app)
        async with AsyncClient(transport=transport, base_url="http://test") as new_client:
            await _login(new_client)
            documents = (await new_client.get("/api/v1/admin/documents")).json()
    assert [(d["filename"], d["status"]) for d in documents] == [("guia.md", "ready")]


@pytest.mark.usefixtures("migrated")
async def test_restore_needs_force_unless_the_database_is_empty(
    app: FastAPI, client: AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    await _chat(client, "Hola")
    archive = await create_portable_backup(settings, tmp_path / "copias")
    await app.state.engine.dispose()

    with pytest.raises(BackupError, match="--force"):
        await restore_portable_backup(settings, archive)

    # Instalación nueva: BD vacía (sin tablas).
    await portable.drop_app_tables(settings.resolved_database_url)
    assert not await portable.database_has_data(settings.resolved_database_url)
    assert await restore_portable_backup(settings, archive) == 0
    assert [c["title"] for c in await _rows(app, "conversations")] == ["Hola"]


async def test_backup_of_an_older_version_is_migrated_after_restoring(
    settings: Settings, tmp_path: Path
) -> None:
    url = settings.resolved_database_url
    # BD de una versión anterior de la aplicación, sin las tablas de la auditoría.
    old_revision = "c4a1e8d2f5b7"
    await portable.migrate(url, old_revision)
    engine = portable._engine(url)
    asked = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO question_log (asked_at, access_kind, question, status, no_info, "
                "sources_retrieved, sources_cited) VALUES (:asked, 'shared', 'Antigua', 'ok', "
                "false, 0, 0)"
            ),
            {"asked": asked},
        )
    await engine.dispose()
    archive = await create_portable_backup(settings, tmp_path / "copias")
    with tarfile.open(archive) as tar:
        manifest = json.loads(tar.extractfile("portable/manifest.json").read())  # type: ignore[union-attr]
    assert manifest["alembic_revision"] == old_revision
    assert "audit_runs" not in manifest["tables"]

    await portable.drop_app_tables(url)
    assert await restore_portable_backup(settings, archive) == 0

    # Restaurada y migrada a la versión actual, con los datos conservados.
    engine = portable._engine(url)
    async with engine.connect() as connection:
        revision = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        row = (
            await connection.execute(
                sa.text("SELECT question, asked_at, client_ip FROM question_log")
            )
        ).one()
        audit_runs = await connection.scalar(sa.text("SELECT count(*) FROM audit_runs"))
    await engine.dispose()
    assert revision == _head()
    assert tuple(row) == ("Antigua", asked, None)
    assert audit_runs == 0


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        ({"app.db": b"", "uploads/a.md": b"x"}, "instalación con SQLite"),
        ({"uploads/a.md": b"x"}, "no contiene la base de datos"),
        ({"portable/manifest.json": b"{"}, "Manifiesto"),
        ({"portable/manifest.json": _manifest(format="otro")}, "Formato"),
        ({"portable/manifest.json": _manifest(alembic_revision="ffffffffffff")}, "más reciente"),
        ({"portable/manifest.json": _manifest(tables={"usuarios": 1})}, "desconocidas"),
        ({"portable/manifest.json": b"{}", "portable/../../x": b"x"}, "no válida"),
    ],
)
async def test_invalid_archives_are_rejected_before_touching_the_database(
    settings: Settings, tmp_path: Path, entries: dict[str, bytes], error: str
) -> None:
    url = settings.resolved_database_url
    await portable.migrate(url, "head")

    with pytest.raises(BackupError, match=error):
        await restore_portable_backup(settings, _archive(tmp_path, entries), force=True)

    assert await portable.database_has_data(url)


def test_cli_with_postgres(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(portable.migrate(settings.resolved_database_url, "head"))

    assert main(["create", "--keep", "5"], settings) == 0
    [archive] = (settings.data_dir / "backups").glob("*.tar.gz")
    assert f"Copia creada: {archive}" in capsys.readouterr().out

    assert main(["restore", str(archive)], settings) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["restore", str(archive), "--force"], settings) == 0
    assert "0 documentos se reindexarán" in capsys.readouterr().out
