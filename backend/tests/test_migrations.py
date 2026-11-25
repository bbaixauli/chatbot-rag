import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import TEST_DATABASE_URL

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # Con la BD de pruebas (PostgreSQL), sobre ella: el conftest la deja vacía.
    url = TEST_DATABASE_URL or f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # configparser

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    # Falla si los modelos tienen cambios sin migración.
    command.check(config)


@pytest.mark.sqlite_only
def test_existing_documents_take_their_filename_as_title(tmp_path: Path) -> None:
    """#75: los documentos existentes se titulan con su nombre de fichero y, como se
    indexaron con él, no quedan desactualizados."""
    database = tmp_path / "t.db"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(config, "b5e8f1c3d7a4")
    with sqlite3.connect(database) as connection:
        for document_id, status in (("d1", "ready"), ("d2", "error")):
            connection.execute(
                "INSERT INTO documents (id, filename, kind, size_bytes, sha256, status, "
                "chunk_count, uploaded_by, created_at, updated_at) "
                "VALUES (?, ?, 'pdf', 1, ?, ?, 0, 'admin', '2026-01-01', '2026-01-01')",
                (document_id, f"NOR-013 {document_id}.pdf", document_id * 32, status),
            )

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id, title, category_id, indexed_title, indexed_category FROM documents "
            "ORDER BY id"
        ).fetchall()
    assert rows == [
        ("d1", "NOR-013 d1.pdf", None, "NOR-013 d1.pdf", None),
        ("d2", "NOR-013 d2.pdf", None, None, None),
    ]
