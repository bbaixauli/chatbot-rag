"""Copias de seguridad de los datos de la aplicación.

Una copia contiene la base de datos SQLite (copiada con la API de backup de SQLite, segura
con la aplicación en marcha) y los ficheros originales subidos. El índice vectorial no se
incluye: se reconstruye a partir de los documentos. Al restaurar, los documentos quedan
pendientes y la aplicación los reindexa automáticamente al arrancar.

Uso (desde `backend/`, o con `docker compose exec backend` en Docker):

    python -m app.backup create [--output DIR] [--keep N]
    python -m app.backup restore FICHERO.tar.gz [--force]
"""

import argparse
import asyncio
import logging
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings
from app.db.models import DocumentStatus

logger = logging.getLogger(__name__)

PREFIX = "chatbot-backup-"
DB_NAME = "app.db"
UPLOADS_NAME = "uploads"


class BackupError(Exception):
    """No se puede crear o restaurar la copia."""


def sqlite_path(settings: Settings) -> Path:
    """Ruta del fichero SQLite; las copias solo admiten SQLite."""
    url = make_url(settings.resolved_database_url)
    if not url.drivername.startswith("sqlite") or not url.database:
        raise BackupError("Las copias de seguridad solo admiten bases de datos SQLite")
    return Path(url.database)


def create_backup(settings: Settings, output_dir: Path, keep: int | None = None) -> Path:
    """Crea `chatbot-backup-<fecha>.tar.gz` en `output_dir` y devuelve su ruta."""
    db_path = sqlite_path(settings)
    if not db_path.exists():
        raise BackupError(f"No existe la base de datos {db_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = output_dir / f"{PREFIX}{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / DB_NAME
        source = sqlite3.connect(db_path)
        destination = sqlite3.connect(db_copy)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        partial = target.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(db_copy, arcname=DB_NAME)
            if settings.uploads_dir.is_dir():
                tar.add(settings.uploads_dir, arcname=UPLOADS_NAME)
        partial.replace(target)

    if keep:
        for old in sorted(output_dir.glob(f"{PREFIX}*.tar.gz"), reverse=True)[keep:]:
            old.unlink()
    return target


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Solo la BD y los ficheros subidos; rechaza rutas absolutas, `..` y enlaces."""
    members = []
    for member in tar.getmembers():
        # Las rutas de un tar usan siempre «/», sea cual sea el sistema.
        path = PurePosixPath(member.name)
        unsafe = path.is_absolute() or ".." in path.parts or any(c in member.name for c in ":\\")
        if unsafe or not (member.isfile() or member.isdir()):
            raise BackupError(f"Entrada no válida en la copia: {member.name}")
        if member.name == DB_NAME or path.parts[0] == UPLOADS_NAME:
            members.append(member)
    if not any(m.name == DB_NAME for m in members):
        raise BackupError("La copia no contiene la base de datos")
    return members


def restore_backup(settings: Settings, archive: Path, force: bool = False) -> int:
    """Restaura una copia en `data_dir`. Devuelve el nº de documentos a reindexar.

    Debe ejecutarse con la aplicación parada.
    """
    db_path = sqlite_path(settings)
    if db_path.exists() and not force:
        raise BackupError(f"Ya existe {db_path}; usa --force para sustituir los datos actuales")
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=settings.data_dir) as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, members=_safe_members(tar), filter="data")
        extracted = Path(tmp)
        # La BD restaurada se prepara antes de tocar los datos actuales.
        connection = sqlite3.connect(extracted / DB_NAME)
        try:
            with connection:
                pending = connection.execute(
                    "UPDATE documents SET status = ?, chunk_count = 0, error = NULL",
                    (DocumentStatus.PENDING.value,),
                ).rowcount
        finally:
            connection.close()

        for leftover in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            leftover.unlink(missing_ok=True)
        shutil.move(extracted / DB_NAME, db_path)
        shutil.rmtree(settings.uploads_dir, ignore_errors=True)
        if (extracted / UPLOADS_NAME).is_dir():
            shutil.move(extracted / UPLOADS_NAME, settings.uploads_dir)
        # El índice se reconstruye al arrancar a partir de los documentos pendientes.
        shutil.rmtree(settings.vector_dir, ignore_errors=True)
    return pending


async def reset_external_index(settings: Settings) -> None:
    """Vacía el índice de Qdrant: sin esto quedarían fragmentos de documentos que no
    están en la copia. (El índice de ChromaDB ya se borra con los datos locales.)"""
    if settings.vector_store != "qdrant":
        return
    from app.services.qdrant_store import QdrantVectorStore

    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    store = QdrantVectorStore.from_url(settings.qdrant_url, api_key, settings.qdrant_collection)
    try:
        await store.reset()
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.backup", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Crea una copia de seguridad")
    create.add_argument(
        "--output", type=Path, help="Carpeta destino (por defecto DATA_DIR/backups)"
    )
    create.add_argument("--keep", type=int, help="Conserva solo las N copias más recientes")
    restore = commands.add_parser("restore", help="Restaura una copia (con la aplicación parada)")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--force", action="store_true", help="Sustituye los datos actuales")
    args = parser.parse_args(argv)

    settings = settings or get_settings()
    try:
        if args.command == "create":
            path = create_backup(settings, args.output or settings.data_dir / "backups", args.keep)
            print(f"Copia creada: {path}")
        else:
            pending = restore_backup(settings, args.archive, args.force)
            if settings.vector_store == "qdrant":
                asyncio.run(reset_external_index(settings))
            print(
                f"Copia restaurada. {pending} documentos se reindexarán al arrancar la aplicación."
            )
    except (BackupError, OSError, tarfile.TarError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
