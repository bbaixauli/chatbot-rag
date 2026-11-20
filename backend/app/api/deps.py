"""Dependencias compartidas de FastAPI."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig


def get_app_settings(request: Request) -> Settings:
    """Configuración asociada a la instancia de la aplicación."""
    settings: Settings = request.app.state.settings
    return settings


def get_runtime_config(request: Request) -> RuntimeConfig:
    """Configuración vigente (editable en la administración) al recibir la petición."""
    config: RuntimeConfig = request.app.state.config.current
    return config


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Sesión de base de datos por petición."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RuntimeConfigDep = Annotated[RuntimeConfig, Depends(get_runtime_config)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
