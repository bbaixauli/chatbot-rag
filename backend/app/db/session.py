"""Motor y sesiones asíncronas de base de datos."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Errores de una BD inaccesible o sin migrar: SQLite da `OperationalError` («no such table»)
# y PostgreSQL `ProgrammingError` (tabla inexistente) u `OperationalError` (sin conexión).
DATABASE_NOT_READY = (OperationalError, ProgrammingError)


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    # SQLite no aplica las claves foráneas (ni ON DELETE CASCADE) si no se activan.
    cursor.execute("PRAGMA foreign_keys=ON")
    # La API y los workers de indexación pueden escribir a la vez: WAL permite leer
    # mientras otro proceso escribe, y la espera evita errores «database is locked».
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def build_engine(database_url: str | URL) -> AsyncEngine:
    """Crea el motor asíncrono para la URL indicada."""
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"
    # Un servidor (PostgreSQL) puede reiniciarse o cerrar conexiones inactivas: se comprueban
    # antes de usarlas en lugar de fallar en la siguiente petición.
    engine = create_async_engine(url, pool_pre_ping=not is_sqlite)
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Crea la factoría de sesiones asociada a un motor."""
    return async_sessionmaker(engine, expire_on_commit=False)
