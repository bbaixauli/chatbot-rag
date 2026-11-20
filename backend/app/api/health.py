"""Endpoint de estado de la aplicación."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession, SettingsDep
from app.core.config import APP_TITLE

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Estado de la aplicación y de sus dependencias."""

    status: Literal["ok", "degraded"]
    app: str
    version: str
    database: Literal["ok", "error"]


@router.get("/health", response_model=HealthResponse)
async def health(db: DbSession, settings: SettingsDep) -> HealthResponse:
    """Comprueba que la API responde y que la base de datos es accesible."""
    database: Literal["ok", "error"]
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        database = "error"
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        app=APP_TITLE,
        version=settings.version,
        database=database,
    )
