"""Punto de entrada de la API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import chat, conversations, health
from app.core.config import Settings, get_settings
from app.db.session import build_engine, build_sessionmaker
from app.services.llm import build_llm_client

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI con la configuración indicada."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(settings.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = build_sessionmaker(engine)
        app.state.llm = build_llm_client(settings)
        yield
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    for router in (health.router, conversations.router, chat.router):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
