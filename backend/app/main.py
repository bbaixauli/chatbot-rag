"""Punto de entrada de la API."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError
from starlette.middleware.sessions import SessionMiddleware

from app.api import admin, appearance, auth, chat, conversations, documents, health
from app.api import audit as audit_api
from app.api import config as config_api
from app.api import dashboard as dashboard_api
from app.core.config import APP_TITLE, Settings, get_settings
from app.core.ldap_auth import ConfigurableLdap
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.rate_limit import RateLimiter
from app.db.session import build_engine, build_sessionmaker
from app.services import audit, dashboard
from app.services.audit import AuditDeps
from app.services.backends import build_task_runner, build_vector_store, close_vector_store
from app.services.documents import DocumentService
from app.services.embeddings import ConfigurableEmbeddings
from app.services.llm import ConfigurableLLM
from app.services.rag import Retriever
from app.services.runtime_config import ConfigStore

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI con la configuración indicada."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    if settings.uses_example_admin_password:
        # En producción ni siquiera arranca (ver Settings); aquí solo se avisa.
        logger.warning("CHATBOT_ADMIN_PASSWORD tiene el valor de ejemplo de .env.example: cámbiala")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(settings.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = build_sessionmaker(engine)
        config = ConfigStore(app.state.sessionmaker, settings)
        try:
            # Primer arranque: copia a la BD la configuración de las variables CHATBOT_*.
            await config.load(seed=True)
        except OperationalError:
            logger.warning("No se pudo leer la configuración de la BD: se usa la de CHATBOT_*")
        app.state.config = config
        # Sigue la configuración de usuarios; no abre conexiones hasta un inicio de sesión.
        app.state.ldap = ConfigurableLdap(config, ca_file=settings.ldap_ca_cert_file)
        app.state.llm = ConfigurableLLM(config)
        app.state.embeddings = ConfigurableEmbeddings(config)
        app.state.vector_store = build_vector_store(settings)
        app.state.retriever = Retriever(app.state.embeddings, app.state.vector_store, config)
        tasks = await build_task_runner(settings)
        app.state.tasks = tasks
        app.state.documents = DocumentService(
            app.state.sessionmaker,
            settings,
            app.state.embeddings,
            app.state.vector_store,
            tasks,
            config,
        )
        try:
            resumed = await app.state.documents.resume_pending()
            if resumed:
                logger.info("Reanudada la indexación de %d documentos", resumed)
            await dashboard.initialize(app.state.sessionmaker)
        except OperationalError:
            logger.warning("No se pudo consultar la BD al arrancar: ¿falta `alembic upgrade head`?")
        app.state.audit = AuditDeps(app.state.sessionmaker, config, settings)
        # La revisión programada la hace la API (no en los tests: sería imprevisible).
        scheduler = (
            asyncio.create_task(audit.scheduler_loop(app.state.audit), name="auditoria")
            if settings.environment != "test"
            else None
        )
        yield
        if scheduler is not None:
            scheduler.cancel()
        for task in list(app.state.audit.tasks):
            task.cancel()
        await tasks.close()
        await close_vector_store(app.state.vector_store)
        await engine.dispose()

    app = FastAPI(title=APP_TITLE, version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
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
        config_api.router,
        documents.router,
        documents.public_router,
        appearance.router,
        appearance.public_router,
        dashboard_api.router,
        audit_api.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
