"""Endpoints de administración: sesión y prompt de sistema."""

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep
from app.core.auth import SESSION_ADMIN_KEY, AdminDep, get_admin, verify_admin_credentials
from app.db.models import SystemPromptVersion
from app.services.prompts import PromptRepository

router = APIRouter(prefix="/admin", tags=["admin"])

# Pausa tras un intento fallido para dificultar ataques de fuerza bruta.
FAILED_LOGIN_DELAY_SECONDS = 0.5


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class SessionOut(BaseModel):
    authenticated: bool
    username: str | None = None
    enabled: bool


class PromptOut(BaseModel):
    content: str
    version_id: int | None
    updated_at: datetime | None
    is_default: bool


class PromptUpdate(BaseModel):
    content: str = Field(min_length=1)


class PromptVersionOut(BaseModel):
    id: int
    content: str
    created_by: str
    created_at: datetime


@router.get("/session", response_model=SessionOut)
async def get_session(request: Request, settings: SettingsDep) -> SessionOut:
    admin = get_admin(request)
    return SessionOut(
        authenticated=admin is not None,
        username=admin.username if admin else None,
        enabled=settings.admin_password is not None,
    )


@router.post("/session", response_model=SessionOut)
async def login(body: LoginRequest, request: Request, settings: SettingsDep) -> SessionOut:
    if settings.admin_password is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La administración no está configurada (falta CHATBOT_ADMIN_PASSWORD)",
        )
    if not verify_admin_credentials(settings, body.username, body.password):
        await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos")
    request.session.clear()
    request.session[SESSION_ADMIN_KEY] = body.username
    return SessionOut(authenticated=True, username=body.username, enabled=True)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _prompt_out(content: str, version: SystemPromptVersion | None) -> PromptOut:
    return PromptOut(
        content=content,
        version_id=version.id if version else None,
        updated_at=version.created_at if version else None,
        is_default=version is None,
    )


@router.get("/prompt", response_model=PromptOut)
async def get_prompt(_: AdminDep, db: DbSession, settings: SettingsDep) -> PromptOut:
    current = await PromptRepository(db, settings.default_system_prompt).current()
    return _prompt_out(current.content, current.version)


@router.put("/prompt", response_model=PromptOut)
async def update_prompt(
    body: PromptUpdate, admin: AdminDep, db: DbSession, settings: SettingsDep
) -> PromptOut:
    content = body.content.strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El prompt no puede estar vacío")
    if len(content) > settings.system_prompt_max_length:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El prompt es demasiado largo")
    version = await PromptRepository(db, settings.default_system_prompt).save(
        content, admin.username
    )
    await db.commit()
    return _prompt_out(version.content, version)


@router.get("/prompt/versions", response_model=list[PromptVersionOut])
async def list_prompt_versions(
    _: AdminDep, db: DbSession, settings: SettingsDep
) -> list[SystemPromptVersion]:
    return await PromptRepository(db, settings.default_system_prompt).list_versions()


@router.post("/prompt/versions/{version_id}/restore", response_model=PromptOut)
async def restore_prompt_version(
    version_id: int, admin: AdminDep, db: DbSession, settings: SettingsDep
) -> PromptOut:
    """Restaura una versión anterior creando una nueva versión con su contenido."""
    repo = PromptRepository(db, settings.default_system_prompt)
    old = await repo.get(version_id)
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versión no encontrada")
    version = await repo.save(old.content, admin.username)
    await db.commit()
    return _prompt_out(version.content, version)
