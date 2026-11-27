"""Endpoint de estado de la aplicación."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession, SettingsDep
from app.core.config import APP_TITLE
from app.db.session import DATABASE_NOT_READY
from app.services.health import HealthChecker, Status

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Estado de la aplicación y de sus dependencias."""

    status: Literal["ok", "degraded"]
    app: str
    version: str
    database: Status
    # Sin ellos, el chat responde sin documentación (#98). Se comprueban como mucho una vez
    # por minuto (ver `services/health.py`).
    vector_store: Status
    embeddings: Status


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, db: DbSession, settings: SettingsDep) -> HealthResponse:
    """Comprueba que la API responde y que la base de datos, el índice vectorial y el servicio
    de embeddings son accesibles. Responde 200 también si algo falla (`degraded`): el
    healthcheck de Docker solo comprueba que la API responda, para no reiniciar el contenedor
    por un fallo de otro servicio."""
    database: Status
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except (SQLAlchemyError, *DATABASE_NOT_READY):
        database = "error"
    checker: HealthChecker = request.app.state.health
    documentation = await checker.documentation()
    return HealthResponse(
        status="ok" if database == "ok" and documentation.ok else "degraded",
        app=APP_TITLE,
        version=settings.version,
        database=database,
        vector_store=documentation.vector_store,
        embeddings=documentation.embeddings,
    )
