"""Actualización periódica de los documentos añadidos por su dirección web (#79).

La API comprueba cada `SCHEDULER_INTERVAL_SECONDS` qué documentos web llevan más de la
frecuencia configurada sin comprobarse y encola su comprobación (`REFRESH_DOCUMENT`), que
hace quien atienda la cola: la propia API o los workers. Cada documento se «reserva» antes
de encolarlo (se anota la comprobación con una actualización condicional), así que no se
encola dos veces aunque haya varios procesos.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.runtime_config import ConfigSource
from app.db.models import Document, DocumentStatus, utcnow
from app.services.tasks import REFRESH_DOCUMENT, TaskRunner

logger = logging.getLogger(__name__)

SCHEDULER_INTERVAL_SECONDS = 300


async def schedule_due(
    sessionmaker: async_sessionmaker[AsyncSession],
    config: ConfigSource,
    tasks: TaskRunner,
    now: datetime | None = None,
) -> int:
    """Encola la comprobación de los documentos web que toca comprobar; devuelve cuántos."""
    web = config.current.web
    if not web.refresh_enabled:
        return 0
    now = now or utcnow()
    cutoff = now - timedelta(hours=web.refresh_interval_hours)
    due = or_(Document.source_checked_at.is_(None), Document.source_checked_at <= cutoff)
    claimed: list[str] = []
    async with sessionmaker() as session:
        candidates = await session.scalars(
            select(Document.id).where(
                Document.source_url.is_not(None),
                # Los que se están indexando se comprueban en la siguiente vuelta.
                Document.status.in_([DocumentStatus.READY.value, DocumentStatus.ERROR.value]),
                due,
            )
        )
        for document_id in list(candidates):
            result = await session.execute(
                update(Document)
                .where(Document.id == document_id, due)
                .values(source_checked_at=now)
            )
            if result.rowcount:  # type: ignore[attr-defined]
                claimed.append(document_id)
        await session.commit()
    version = now.strftime("%Y%m%dT%H%M%S%fZ")
    for document_id in claimed:
        await tasks.enqueue(REFRESH_DOCUMENT, document_id, version)
    if claimed:
        logger.info("Comprobación de %d documentos web programada", len(claimed))
    return len(claimed)


async def scheduler_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    config: ConfigSource,
    tasks: TaskRunner,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Bucle de la API: cada pocos minutos encola las comprobaciones pendientes."""
    while True:
        try:
            await schedule_due(sessionmaker, config, tasks)
        except Exception:
            logger.exception("Error en la programación de la actualización de documentos web")
        await sleep(SCHEDULER_INTERVAL_SECONDS)
