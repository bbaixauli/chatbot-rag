"""Ejecución de tareas en segundo plano.

Las tareas se identifican por un nombre de trabajo y argumentos de texto (serializables),
de modo que pueden ejecutarse en el propio proceso o enviarse a una cola:

- `InProcessTaskRunner` (por defecto): tareas asyncio en el bucle de eventos de la API.
- `ArqTaskRunner` (`CHATBOT_TASK_RUNNER=arq`): cola en Redis atendida por uno o varios
  workers (`arq app.worker.WorkerSettings`).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from arq.connections import ArqRedis

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]
JobHandler = Callable[[str], Awaitable[None]]

# Trabajos que conoce el worker de arq (ver app/worker.py).
INDEX_DOCUMENT = "index_document"


class TaskRunner(Protocol):
    def register(self, job: str, handler: JobHandler) -> None:
        """Asocia un trabajo con la función que lo ejecuta (en el proceso que lo atiende)."""
        ...

    async def enqueue(self, job: str, argument: str, version: str | None = None) -> None:
        """Programa un trabajo y vuelve inmediatamente.

        `version` identifica la petición: dos trabajos con el mismo argumento y la misma
        versión son el mismo trabajo (se puede deduplicar); con otra versión, es una
        petición nueva que no debe descartarse aunque el anterior siga en curso.
        """
        ...

    async def drain(self) -> None:
        """Espera a que terminen las tareas en curso (si se ejecutan en este proceso)."""
        ...

    async def close(self) -> None:
        """Cancela o libera lo que mantenga abierto el ejecutor."""
        ...


class InProcessTaskRunner:
    """Tareas asyncio en el mismo proceso, con un máximo de tareas simultáneas."""

    def __init__(self, max_concurrency: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()
        self._handlers: dict[str, JobHandler] = {}

    async def _run(self, name: str, factory: TaskFactory) -> None:
        async with self._semaphore:
            try:
                await factory()
            except Exception:
                logger.exception("La tarea %s ha fallado", name)

    def submit(self, name: str, factory: TaskFactory) -> None:
        task = asyncio.create_task(self._run(name, factory), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def register(self, job: str, handler: JobHandler) -> None:
        self._handlers[job] = handler

    async def enqueue(self, job: str, argument: str, version: str | None = None) -> None:
        handler = self._handlers[job]
        self.submit(f"{job}-{argument}", lambda: handler(argument))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def cancel_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.cancel_all()


class ArqTaskRunner:
    """Envía los trabajos a la cola de Redis; los ejecuta el worker de arq."""

    def __init__(self, pool: "ArqRedis") -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, redis_url: str) -> "ArqTaskRunner":
        from arq import create_pool
        from arq.connections import RedisSettings

        return cls(await create_pool(RedisSettings.from_dsn(redis_url)))

    def register(self, job: str, handler: JobHandler) -> None:
        """Los trabajos los ejecuta el worker, que tiene sus propias funciones."""

    async def enqueue(self, job: str, argument: str, version: str | None = None) -> None:
        # El id evita encolar dos veces el mismo trabajo mientras está pendiente o en curso
        # (p. ej. al reiniciar la API con documentos pendientes). Incluye la versión de la
        # petición: si no, una reindexación pedida mientras un worker procesa el documento
        # se descartaría y el índice podría quedarse sin sus fragmentos.
        job_id = f"{job}:{argument}" if version is None else f"{job}:{argument}:{version}"
        queued = await self._pool.enqueue_job(job, argument, _job_id=job_id)
        if queued is None:
            logger.info("El trabajo %s ya estaba en la cola", job_id)

    async def drain(self) -> None:
        """Los trabajos se ejecutan en otro proceso: no hay nada que esperar aquí."""

    async def close(self) -> None:
        await self._pool.aclose()
