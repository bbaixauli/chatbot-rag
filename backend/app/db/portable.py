"""Volcado y carga de la base de datos en un formato portable (copias con PostgreSQL, #60).

Una copia portable es un directorio con:

- `manifest.json`: formato, versión, revisión de Alembic y número de filas de cada tabla.
- `<tabla>.jsonl`: una fila por línea, en JSON (fechas en ISO 8601).

El volcado se hace en una única transacción de solo lectura con aislamiento REPEATABLE READ:
todas las tablas reflejan el mismo instante aunque la aplicación siga en marcha.

Para cargarla, la base de datos se migra hasta la revisión de la copia (así las columnas
coinciden con las del volcado), se insertan las filas en orden de claves foráneas y se ajustan
las secuencias de los ids. Después se aplican las migraciones posteriores: una copia de una
versión anterior de la aplicación se restaura en una más reciente.
"""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.db.base import Base

FORMAT = "chatbot-portable"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
ALEMBIC_TABLE = "alembic_version"
BACKEND_DIR = Path(__file__).resolve().parents[2]
# Filas por sentencia INSERT al cargar.
BATCH_SIZE = 500


class PortableBackupError(Exception):
    """La copia portable no se puede crear o cargar."""


def _engine(url: URL) -> AsyncEngine:
    # Sin pool: son operaciones puntuales (y la restauración se hace con la aplicación parada).
    return create_async_engine(url, poolclass=sa.pool.NullPool)


def _alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = url
    return config


def is_known_revision(revision: str) -> bool:
    """True si la revisión existe en las migraciones de esta versión de la aplicación."""
    script = ScriptDirectory.from_config(_alembic_config(URL.create("sqlite")))
    try:
        return script.get_revision(revision) is not None
    except CommandError:
        return False


async def migrate(url: URL, revision: str) -> None:
    """`alembic upgrade <revision>` sobre la BD de `url`.

    En otro hilo: el entorno de Alembic ejecuta su propio bucle de asyncio.
    """
    await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)


def _existing_app_tables(connection: Connection) -> list[str]:
    """Tablas de la aplicación presentes en la BD (no toca otras del mismo esquema)."""
    present = set(sa.inspect(connection).get_table_names())
    return [name for name in Base.metadata.tables if name in present]


async def _reflect(connection: AsyncConnection, names: list[str]) -> sa.MetaData:
    metadata = sa.MetaData()
    await connection.run_sync(lambda sync: metadata.reflect(sync, only=names))
    return metadata


def _encode(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Tipo no admitido en la copia: {type(value).__name__}")


def _decoders(table: sa.Table) -> dict[str, type[datetime] | type[date]]:
    """Columnas cuyo valor JSON (texto ISO) hay que convertir al cargarlas."""
    decoders: dict[str, type[datetime] | type[date]] = {}
    for column in table.columns:
        if isinstance(column.type, sa.DateTime):
            decoders[column.name] = datetime
        elif isinstance(column.type, sa.Date):
            decoders[column.name] = date
    return decoders


async def dump_database(url: URL, target: Path) -> dict[str, Any]:
    """Vuelca las tablas de la aplicación en `target` y devuelve el manifiesto.

    Lee y escribe ficheros sin delegarlo en hilos: se ejecuta desde la línea de comandos, en
    un bucle de asyncio propio que no atiende peticiones.
    """
    target.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    engine = _engine(url)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            )
            async with connection.begin():
                names = await connection.run_sync(_existing_app_tables)
                if ALEMBIC_TABLE not in await connection.run_sync(
                    lambda sync: sa.inspect(sync).get_table_names()
                ):
                    raise PortableBackupError(
                        "La base de datos no está migrada: arranca la aplicación antes de copiarla"
                    )
                revision = await connection.scalar(
                    sa.text(f"SELECT version_num FROM {ALEMBIC_TABLE}")  # noqa: S608
                )
                metadata = await _reflect(connection, names)
                counts: dict[str, int] = {}
                for table in metadata.sorted_tables:
                    counts[table.name] = await _dump_table(connection, table, target)
    finally:
        await engine.dispose()

    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "dialect": url.get_backend_name(),
        "alembic_revision": revision,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": counts,
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


async def _dump_table(connection: AsyncConnection, table: sa.Table, target: Path) -> int:
    rows = 0
    order = list(table.primary_key.columns) or list(table.columns)
    result = await connection.stream(sa.select(table).order_by(*order))
    with (target / f"{table.name}.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        async for row in result:
            out.write(json.dumps(dict(row._mapping), default=_encode, ensure_ascii=False))
            out.write("\n")
            rows += 1
    return rows


def read_manifest(source: Path) -> dict[str, Any]:
    """Lee y valida el manifiesto de una copia portable."""
    try:
        manifest: dict[str, Any] = json.loads((source / MANIFEST_NAME).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise PortableBackupError(f"Manifiesto de la copia no válido: {exc}") from exc
    if manifest.get("format") != FORMAT or manifest.get("version") != FORMAT_VERSION:
        raise PortableBackupError("Formato de copia no reconocido")
    revision = manifest.get("alembic_revision")
    if not isinstance(revision, str) or not is_known_revision(revision):
        raise PortableBackupError(
            f"La copia es de una versión de la aplicación más reciente (revisión {revision}): "
            "actualiza la aplicación antes de restaurarla"
        )
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not set(tables) <= set(Base.metadata.tables):
        raise PortableBackupError("La copia contiene tablas desconocidas")
    return manifest


async def database_has_data(url: URL) -> bool:
    """True si la BD ya tiene tablas de la aplicación (o está migrada)."""
    engine = _engine(url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    finally:
        await engine.dispose()
    return ALEMBIC_TABLE in names or any(name in Base.metadata.tables for name in names)


async def drop_app_tables(url: URL) -> None:
    """Elimina las tablas de la aplicación y la de versiones de Alembic (solo esas)."""
    engine = _engine(url)
    try:
        async with engine.begin() as connection:
            names = [*await connection.run_sync(_existing_app_tables), ALEMBIC_TABLE]
            quote = connection.dialect.identifier_preparer.quote
            for name in names:
                await connection.execute(sa.text(f"DROP TABLE IF EXISTS {quote(name)} CASCADE"))
    finally:
        await engine.dispose()


def _rows(source: TextIO, decoders: dict[str, type[datetime] | type[date]]) -> Iterator[Any]:
    for line in source:
        if not line.strip():
            continue
        row = json.loads(line)
        for name, kind in decoders.items():
            if row.get(name) is not None:
                row[name] = kind.fromisoformat(row[name])
        yield row


async def load_database(url: URL, source: Path, manifest: dict[str, Any]) -> None:
    """Inserta las filas de la copia en la BD, ya migrada a la revisión de la copia.

    Todo en una transacción: si algo falla, la BD queda con las tablas vacías.
    """
    names = list(manifest["tables"])
    engine = _engine(url)
    try:
        async with engine.begin() as connection:
            metadata = await _reflect(connection, names)
            missing = set(names) - set(metadata.tables)
            if missing:
                raise PortableBackupError(f"Faltan tablas en la base de datos: {sorted(missing)}")
            tables = metadata.sorted_tables
            # Por si alguna migración hubiera sembrado filas.
            for table in reversed(tables):
                await connection.execute(table.delete())
            for table in tables:
                await _load_table(connection, table, source)
            await _reset_sequences(connection, tables)
    finally:
        await engine.dispose()


async def _load_table(connection: AsyncConnection, table: sa.Table, source: Path) -> None:
    path = source / f"{table.name}.jsonl"
    if not path.exists():
        raise PortableBackupError(f"Falta {path.name} en la copia")
    batch: list[Any] = []
    with path.open(encoding="utf-8") as rows:
        for row in _rows(rows, _decoders(table)):
            batch.append(row)
            if len(batch) == BATCH_SIZE:
                await connection.execute(table.insert(), batch)
                batch = []
    if batch:
        await connection.execute(table.insert(), batch)


async def _reset_sequences(connection: AsyncConnection, tables: list[sa.Table]) -> None:
    """Las filas se insertan con sus ids: las secuencias deben continuar tras el mayor."""
    if connection.dialect.name != "postgresql":
        return
    quote = connection.dialect.identifier_preparer.quote
    for table in tables:
        for column in table.primary_key.columns:
            if not isinstance(column.type, sa.Integer):
                continue
            sequence = await connection.scalar(
                sa.select(sa.func.pg_get_serial_sequence(quote(table.name), column.name))
            )
            if sequence is None:
                continue
            # Con la tabla vacía, el siguiente id es 1.
            await connection.execute(
                sa.text(
                    f"SELECT setval(:sequence, COALESCE(MAX({quote(column.name)}), 0) + 1, false) "  # noqa: S608
                    f"FROM {quote(table.name)}"
                ),
                {"sequence": sequence},
            )
