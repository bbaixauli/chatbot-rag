"""Estado del índice vectorial y del servicio de embeddings para `/health` (#98).

Si cualquiera de los dos falla, el chat responde sin documentación (#95). Se comprueban con
una operación mínima (contar los fragmentos y calcular un vector) y con un tiempo de espera
corto, para que `/health` responda enseguida aunque un servicio no conteste. El resultado se
guarda un rato: la insignia de estado consulta `/health` en cada carga de página y el servicio
de embeddings puede ser de pago o estar compartido.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.services.embeddings import Embeddings
from app.services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

Status = Literal["ok", "error"]

# Cuánto se reutiliza una comprobación y cuánto se espera a cada servicio.
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class DocumentationHealth:
    vector_store: Status
    embeddings: Status

    @property
    def ok(self) -> bool:
        return self.vector_store == "ok" and self.embeddings == "ok"


class HealthChecker:
    """Comprueba el índice y los embeddings como mucho una vez cada `ttl` segundos."""

    def __init__(
        self,
        store: VectorStore,
        embeddings: Embeddings,
        *,
        ttl: float = CACHE_SECONDS,
        timeout: float = TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._embeddings = embeddings
        self._ttl = ttl
        self._timeout = timeout
        self._clock = clock
        self._result: DocumentationHealth | None = None
        self._checked_at = 0.0
        # Varias peticiones a la vez esperan a la misma comprobación.
        self._lock = asyncio.Lock()

    async def documentation(self) -> DocumentationHealth:
        async with self._lock:
            if self._result is None or self._clock() - self._checked_at >= self._ttl:
                store, embeddings = await asyncio.gather(
                    self._check("índice vectorial", self._store.count),
                    self._check("embeddings", self._embed),
                )
                self._result = DocumentationHealth(store, embeddings)
                self._checked_at = self._clock()
            return self._result

    async def _embed(self) -> None:
        [vector] = await self._embeddings.embed(["comprobación de estado"])
        if not vector:
            raise ValueError("el servicio devolvió un vector vacío")

    async def _check(self, name: str, operation: Callable[[], Awaitable[object]]) -> Status:
        try:
            await asyncio.wait_for(operation(), self._timeout)
        except TimeoutError:
            logger.warning("Estado: %s no ha respondido en %s s", name, self._timeout)
            return "error"
        except Exception as exc:
            logger.warning("Estado: falla %s: %s", name, exc)
            return "error"
        return "ok"
