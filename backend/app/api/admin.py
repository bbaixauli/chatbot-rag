"""Endpoints de administración: sesión, prompt de sistema, inicio del chat y acceso."""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.auth import ldap_login
from app.api.deps import DbSession, SettingsDep
from app.api.schemas import ChatConfigOut
from app.core.auth import SESSION_ADMIN_KEY, AdminDep, get_admin, verify_admin_credentials
from app.core.config import Settings
from app.core.rate_limit import LoginRateLimit
from app.db.models import SystemPromptVersion
from app.services.access import AccessConfig, AccessRepository
from app.services.chat_config import MAX_SUGGESTED_QUESTIONS, ChatConfig, ChatConfigRepository
from app.services.prompts import PromptRepository

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

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


class ChatConfigUpdate(BaseModel):
    welcome_message: str | None = None
    suggested_questions: list[str] = Field(default_factory=list, max_length=MAX_SUGGESTED_QUESTIONS)


class AccessOut(BaseModel):
    anonymous_allowed: bool
    # El acceso anónimo solo tiene sentido con LDAP (sin LDAP, el chat ya es anónimo).
    available: bool


class AccessUpdate(BaseModel):
    anonymous_allowed: bool


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
        enabled=_admin_enabled(settings),
    )


def _admin_enabled(settings: Settings) -> bool:
    """Hay cuenta local o administradores del directorio."""
    ldap_admins = settings.auth_mode == "ldap" and bool(settings.ldap_admin_group_dn)
    return settings.admin_password is not None or ldap_admins


@router.post("/session", response_model=SessionOut)
async def login(
    body: LoginRequest, request: Request, settings: SettingsDep, _: LoginRateLimit
) -> SessionOut:
    """Cuenta local de administración o, con LDAP, usuario del grupo de administración."""
    if not _admin_enabled(settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La administración no está configurada (falta CHATBOT_ADMIN_PASSWORD)",
        )
    is_local = settings.admin_password is not None and body.username == settings.admin_username
    if is_local or settings.auth_mode != "ldap":
        # Las credenciales de la cuenta local nunca se envían al directorio.
        if not verify_admin_credentials(settings, body.username, body.password):
            await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos")
        request.session.clear()
        request.session[SESSION_ADMIN_KEY] = body.username
        return SessionOut(authenticated=True, username=body.username, enabled=True)

    user = await ldap_login(request, body.username, body.password)
    if not user.is_admin:
        # La sesión del chat queda iniciada, pero sin acceso a la administración.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Tu usuario no tiene permisos de administración"
        )
    return SessionOut(authenticated=True, username=user.id, enabled=True)


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


@router.get("/chat-config", response_model=ChatConfigOut)
async def get_chat_config(_: AdminDep, db: DbSession) -> ChatConfig:
    return await ChatConfigRepository(db).get()


@router.put("/chat-config", response_model=ChatConfigOut)
async def update_chat_config(
    body: ChatConfigUpdate, admin: AdminDep, db: DbSession, settings: SettingsDep
) -> ChatConfig:
    """Guarda el mensaje inicial y las preguntas sugeridas (los textos vacíos se descartan)."""
    welcome = (body.welcome_message or "").strip() or None
    if welcome and len(welcome) > settings.chat_welcome_max_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "El mensaje inicial es demasiado largo"
        )
    questions = [q.strip() for q in body.suggested_questions if q.strip()]
    if any(len(q) > settings.chat_suggested_question_max_length for q in questions):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Alguna pregunta sugerida es demasiado larga"
        )
    config = await ChatConfigRepository(db).save(
        ChatConfig(welcome_message=welcome, suggested_questions=questions), admin.username
    )
    await db.commit()
    return config


def _access_out(settings: Settings, config: AccessConfig) -> AccessOut:
    return AccessOut(
        anonymous_allowed=config.anonymous_allowed, available=settings.auth_mode == "ldap"
    )


@router.get("/access", response_model=AccessOut)
async def get_access(_: AdminDep, db: DbSession, settings: SettingsDep) -> AccessOut:
    return _access_out(settings, await AccessRepository(db).get())


@router.put("/access", response_model=AccessOut)
async def update_access(
    body: AccessUpdate, admin: AdminDep, db: DbSession, settings: SettingsDep
) -> AccessOut:
    """Permite o impide el acceso anónimo. Al impedirlo, las sesiones anónimas abiertas
    dejan de funcionar en su siguiente petición."""
    if settings.auth_mode != "ldap":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El acceso anónimo solo se configura con LDAP"
        )
    config = await AccessRepository(db).save(
        AccessConfig(anonymous_allowed=body.anonymous_allowed), admin.username
    )
    await db.commit()
    logger.info(
        "Acceso anónimo %s",
        "permitido" if config.anonymous_allowed else "desactivado",
        extra={"user_id": admin.username},
    )
    return _access_out(settings, config)
