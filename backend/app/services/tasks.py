"""Ejecución de tareas en segundo plano.

`InProcessTaskRunner` usa el bucle de eventos de la propia API. Si el volumen crece,
se puede sustituir por una cola (arq + Redis) implementando la misma interfaz.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]


class TaskRunner(Protocol):
    def submit(self, name: str, factory: TaskFactory) -> None:
        """Programa una tarea y vuelve inmediatamente."""
        ...

    async def drain(self) -> None:
        """Espera a que terminen las tareas en curso."""
        ...


class InProcessTaskRunner:
    """Tareas asyncio en el mismo proceso, con un máximo de tareas simultáneas."""

    def __init__(self, max_concurrency: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

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

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def cancel_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
