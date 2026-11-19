"""Punto de entrada de la API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError
from starlette.middleware.sessions import SessionMiddleware

from app.api import admin, auth, chat, conversations, documents, health
from app.core.config import Settings, get_settings
from app.core.ldap_auth import LdapAuthenticator
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.rate_limit import RateLimiter
from app.db.session import build_engine, build_sessionmaker
from app.services.backends import build_task_runner, build_vector_store, close_vector_store
from app.services.documents import DocumentService
from app.services.embeddings import build_embeddings
from app.services.llm import build_llm_client
from app.services.rag import Retriever

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI con la configuración indicada."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(settings.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = build_sessionmaker(engine)
        app.state.llm = build_llm_client(settings)
        app.state.embeddings = build_embeddings(settings)
        app.state.vector_store = build_vector_store(settings)
        app.state.retriever = Retriever(app.state.embeddings, app.state.vector_store, settings)
        tasks = await build_task_runner(settings)
        app.state.tasks = tasks
        app.state.documents = DocumentService(
            app.state.sessionmaker,
            settings,
            app.state.embeddings,
            app.state.vector_store,
            tasks,
        )
        try:
            resumed = await app.state.documents.resume_pending()
            if resumed:
                logger.info("Reanudada la indexación de %d documentos", resumed)
        except OperationalError:
            logger.warning("No se pudo consultar la BD al arrancar: ¿falta `alembic upgrade head`?")
        yield
        await tasks.close()
        await close_vector_store(app.state.vector_store)
        await engine.dispose()

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
    # No abre conexiones al crearse: solo al validar un inicio de sesión.
    app.state.ldap = LdapAuthenticator(settings) if settings.auth_mode == "ldap" else None
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie="chatbot_session",
        max_age=settings.session_max_age_seconds,
        same_site="strict",
        https_only=settings.session_cookie_secure,
    )
    # El último middleware añadido es el más externo: el id de petición abarca todo.
    app.add_middleware(RequestContextMiddleware)
    for router in (
        health.router,
        auth.router,
        conversations.router,
        chat.router,
        admin.router,
        documents.router,
        documents.public_router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
