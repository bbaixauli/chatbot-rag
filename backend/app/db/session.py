"""Motor y sesiones asíncronas de base de datos."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    # SQLite no aplica las claves foráneas (ni ON DELETE CASCADE) si no se activan.
    cursor.execute("PRAGMA foreign_keys=ON")
    # La API y los workers de indexación pueden escribir a la vez: WAL permite leer
    # mientras otro proceso escribe, y la espera evita errores «database is locked».
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def build_engine(database_url: str) -> AsyncEngine:
    """Crea el motor asíncrono para la URL indicada."""
    engine = create_async_engine(database_url)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Crea la factoría de sesiones asociada a un motor."""
    return async_sessionmaker(engine, expire_on_commit=False)
