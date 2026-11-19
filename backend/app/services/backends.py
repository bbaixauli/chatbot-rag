"""Construcción del índice vectorial y del ejecutor de tareas según la configuración."""

from app.core.config import Settings
from app.services.tasks import InProcessTaskRunner, TaskRunner
from app.services.vectorstore import ChromaVectorStore, VectorStore


def build_vector_store(settings: Settings) -> VectorStore:
    """ChromaDB embebido (por defecto) o Qdrant."""
    if settings.vector_store == "qdrant":
        from app.services.qdrant_store import QdrantVectorStore

        api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
        return QdrantVectorStore.from_url(settings.qdrant_url, api_key, settings.qdrant_collection)
    return ChromaVectorStore(settings.vector_dir)


async def build_task_runner(settings: Settings) -> TaskRunner:
    """Tareas en el propio proceso (por defecto) o cola de Redis con arq."""
    if settings.task_runner == "arq":
        from app.services.tasks import ArqTaskRunner

        return await ArqTaskRunner.connect(settings.redis_url)
    return InProcessTaskRunner(settings.indexing_concurrency)


async def close_vector_store(store: VectorStore) -> None:
    """Cierra las conexiones del índice, si las tiene (Qdrant)."""
    close = getattr(store, "close", None)
    if close is not None:
        await close()
