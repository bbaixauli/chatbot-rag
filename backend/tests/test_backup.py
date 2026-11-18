import io
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.backup import BackupError, create_backup, main, restore_backup, sqlite_path
from app.core.config import Settings
from app.main import create_app
from tests.factories import MARKDOWN

CREDENTIALS = {"username": "admin", "password": "secreto"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


async def _login(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/admin/session", json=CREDENTIALS)).status_code == 200


async def _index_document(app: FastAPI, client: AsyncClient) -> None:
    await _login(client)
    response = await client.post(
        "/api/v1/admin/documents", files=[("files", ("guia.md", MARKDOWN.encode()))]
    )
    assert response.status_code == 200
    await app.state.tasks.drain()
    await client.post("/api/v1/chat", json={"message": "Conversación previa"})


def _restored_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path / "restaurado",
        llm_provider="fake",
        embedding_provider="fake",
        admin_password=SecretStr("secreto"),
    )


async def test_backup_and_restore_round_trip(
    app: FastAPI, client: AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    await _index_document(app, client)

    # Se crea con la aplicación en marcha.
    archive = create_backup(settings, tmp_path / "copias")

    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "app.db" in names
    assert any(name.startswith("uploads/") and name.endswith(".md") for name in names)
    assert not any("chroma" in name for name in names)

    restored = _restored_settings(tmp_path)
    assert restore_backup(restored, archive) == 1
    assert not restored.vector_dir.exists()

    # Al arrancar, la aplicación reindexa los documentos restaurados.
    new_app = create_app(restored)
    async with new_app.router.lifespan_context(new_app):
        await new_app.state.tasks.drain()
        transport = ASGITransport(app=new_app)
        async with AsyncClient(transport=transport, base_url="http://test") as new_client:
            await _login(new_client)
            documents = (await new_client.get("/api/v1/admin/documents")).json()
            conversations = (await new_client.get("/api/v1/conversations")).json()

    assert [(d["filename"], d["status"]) for d in documents] == [("guia.md", "ready")]
    assert documents[0]["chunk_count"] > 0
    assert [c["title"] for c in conversations] == ["Conversación previa"]


async def test_restore_refuses_to_overwrite_without_force(
    app: FastAPI, client: AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    await _index_document(app, client)
    archive = create_backup(settings, tmp_path / "copias")
    restored = _restored_settings(tmp_path)
    restore_backup(restored, archive)
    (restored.uploads_dir / "sobrante.txt").write_text("x")

    with pytest.raises(BackupError, match="--force"):
        restore_backup(restored, archive)

    assert restore_backup(restored, archive, force=True) == 1
    assert not (restored.uploads_dir / "sobrante.txt").exists()


async def test_keep_rotates_old_backups(app: FastAPI, settings: Settings, tmp_path: Path) -> None:
    output = tmp_path / "copias"
    output.mkdir()
    for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
        (output / f"chatbot-backup-{stamp}.tar.gz").write_bytes(b"")

    newest = create_backup(settings, output, keep=2)

    assert sorted(p.name for p in output.iterdir()) == [
        "chatbot-backup-20200103-000000.tar.gz",
        newest.name,
    ]


def _archive(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    path = tmp_path / "manipulada.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        ({"app.db": b"", "../fuera.txt": b"x"}, "no válida"),
        ({"/etc/passwd": b"x", "app.db": b""}, "no válida"),
        ({"app.db": b"", r"C:\Windows\x.txt": b"x"}, "no válida"),
        ({"app.db": b"", r"uploads\..\..\x.txt": b"x"}, "no válida"),
        ({"uploads/a.md": b"x"}, "no contiene la base de datos"),
    ],
)
def test_restore_rejects_unsafe_or_incomplete_archives(
    tmp_path: Path, entries: dict[str, bytes], error: str
) -> None:
    with pytest.raises(BackupError, match=error):
        restore_backup(_restored_settings(tmp_path), _archive(tmp_path, entries))


def test_only_sqlite_is_supported(tmp_path: Path) -> None:
    settings = _restored_settings(tmp_path)
    settings.database_url = "postgresql+asyncpg://u:p@db/chatbot"
    with pytest.raises(BackupError, match="SQLite"):
        sqlite_path(settings)
    with pytest.raises(BackupError, match="No existe"):
        create_backup(_restored_settings(tmp_path), tmp_path / "copias")


async def test_cli(
    app: FastAPI,
    settings: Settings,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["create", "--keep", "5"], settings) == 0
    [archive] = (settings.data_dir / "backups").glob("*.tar.gz")
    assert f"Copia creada: {archive}" in capsys.readouterr().out

    restored = _restored_settings(tmp_path)
    assert main(["restore", str(archive)], restored) == 0
    assert "0 documentos se reindexarán" in capsys.readouterr().out

    assert main(["restore", str(archive)], restored) == 1
    assert "--force" in capsys.readouterr().err
