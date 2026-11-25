"""Worker de arq: indexa los documentos que la API deja en la cola de Redis.

Uso (con CHATBOT_TASK_RUNNER=arq y CHATBOT_VECTOR_STORE=qdrant):

    arq app.worker.WorkerSettings

Se pueden arrancar varios workers; cada uno indexa hasta `CHATBOT_INDEXING_CONCURRENCY`
documentos a la vez.
"""

import logging
from typing import Any

from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import build_engine, build_sessionmaker
from app.services.backends import build_vector_store, close_vector_store
from app.services.documents import DocumentService
from app.services.embeddings import ConfigurableEmbeddings
from app.services.runtime_config import ConfigStore
from app.services.tasks import INDEX_DOCUMENT, REFRESH_DOCUMENT, ArqTaskRunner

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings: Settings = ctx.get("settings") or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    engine = build_engine(settings.resolved_database_url)
    sessionmaker = build_sessionmaker(engine)
    store = build_vector_store(settings)
    # La API copia la configuración a la BD al arrancar; el worker solo la lee.
    config = ConfigStore(sessionmaker, settings)
    await config.reload()
    ctx["engine"] = engine
    ctx["vector_store"] = store
    ctx["config"] = config
    # Lo que encola el worker (la indexación de un documento web que ha cambiado, #79)
    # vuelve a la cola de Redis.
    ctx["documents"] = DocumentService(
        sessionmaker,
        settings,
        ConfigurableEmbeddings(config),
        store,
        ArqTaskRunner(ctx["redis"]),
        config,
    )
    logger.info("Worker de indexación preparado")


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_vector_store(ctx["vector_store"])
    await ctx["engine"].dispose()


async def index_document(ctx: dict[str, Any], document_id: str) -> None:
    # La configuración (modelo de embeddings, troceado) puede haber cambiado en la
    # administración desde el último trabajo.
    config: ConfigStore = ctx["config"]
    await config.reload()
    documents: DocumentService = ctx["documents"]
    await documents.process(document_id)


async def refresh_document(ctx: dict[str, Any], document_id: str) -> None:
    """Comprueba si ha cambiado un documento web (#79); si cambia, encola su indexación."""
    config: ConfigStore = ctx["config"]
    await config.reload()
    documents: DocumentService = ctx["documents"]
    await documents.refresh(document_id)


class WorkerSettings:
    """Configuración del worker (la lee el comando `arq`)."""

    _settings = get_settings()

    functions = [  # noqa: RUF012
        func(index_document, name=INDEX_DOCUMENT),
        func(refresh_document, name=REFRESH_DOCUMENT),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.indexing_concurrency
    # Sin guardar resultados: un documento se puede volver a encolar en cuanto termina.
    keep_result = 0
    # Documentos grandes con muchos fragmentos: margen amplio para los embeddings.
    job_timeout = 1800
    # Si un worker muere a mitad, el trabajo se reintenta.
    max_tries = 3
    health_check_interval = 30
