"""Motor y sesiones asíncronas de base de datos."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(database_url: str) -> AsyncEngine:
    """Crea el motor asíncrono para la URL indicada."""
    return create_async_engine(database_url)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Crea la factoría de sesiones asociada a un motor."""
    return async_sessionmaker(engine, expire_on_commit=False)
