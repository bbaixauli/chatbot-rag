"""Endpoints de administración: sesión, prompt de sistema, inicio del chat y acceso."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.auth import ldap_login
from app.api.deps import DbSession, RuntimeConfigDep, SettingsDep
from app.api.schemas import ChatConfigOut
from app.core.auth import (
    SESSION_ADMIN_KEY,
    AdminDep,
    AdminUser,
    get_staff,
    verify_admin_credentials,
)
from app.core.config import Settings
from app.core.rate_limit import LoginRateLimit
from app.core.runtime_config import RuntimeConfig, UsersConfig
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
    # Roles: un auditor sin administración solo ve la auditoría.
    is_admin: bool = False
    is_auditor: bool = False


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
    # Indica qué ajuste se ofrece: `anonymous_allowed` con LDAP, `private_sessions` sin él.
    mode: Literal["none", "ldap"]
    anonymous_allowed: bool
    private_sessions: bool


class AccessUpdate(BaseModel):
    """Solo se puede cambiar el ajuste que corresponde al modo de autenticación."""

    anonymous_allowed: bool | None = None
    private_sessions: bool | None = None


class PromptVersionOut(BaseModel):
    id: int
    content: str
    created_by: str
    created_at: datetime


@router.get("/session", response_model=SessionOut)
async def get_session(
    request: Request, settings: SettingsDep, config: RuntimeConfigDep
) -> SessionOut:
    staff = get_staff(request, config.users)
    return _session_out(staff, admin_enabled(settings, config.users))


def _session_out(staff: AdminUser | None, enabled: bool) -> SessionOut:
    return SessionOut(
        authenticated=staff is not None,
        username=staff.username if staff else None,
        enabled=enabled,
        is_admin=staff.is_admin if staff else False,
        is_auditor=staff.is_auditor if staff else False,
    )


def admin_enabled(settings: Settings, users: UsersConfig) -> bool:
    """Hay cuenta local o administradores (o auditores) del directorio."""
    ldap_groups = users.auth_mode == "ldap" and bool(
        users.ldap_admin_group_dn or users.ldap_auditor_group_dn
    )
    return settings.admin_password is not None or ldap_groups


@router.post("/session", response_model=SessionOut)
async def login(
    body: LoginRequest,
    request: Request,
    settings: SettingsDep,
    config: RuntimeConfigDep,
    _: LoginRateLimit,
) -> SessionOut:
    """Cuenta local de administración o, con LDAP, usuario del grupo de administración."""
    ldap = config.users.auth_mode == "ldap"
    if not admin_enabled(settings, config.users):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La administración no está configurada (falta CHATBOT_ADMIN_PASSWORD)",
        )
    is_local = settings.admin_password is not None and body.username == settings.admin_username
    if is_local or not ldap:
        # Las credenciales de la cuenta local nunca se envían al directorio.
        if not verify_admin_credentials(settings, body.username, body.password):
            await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos")
        if ldap:
            request.session.clear()
        else:
            # Sin LDAP, la sesión privada del chat (si la hay) es independiente: se conserva.
            request.session.pop(SESSION_ADMIN_KEY, None)
        request.session[SESSION_ADMIN_KEY] = body.username
        return _session_out(get_staff(request, config.users), True)

    await ldap_login(request, body.username, body.password)
    staff = get_staff(request, config.users)
    if staff is None:
        # La sesión del chat queda iniciada, pero sin acceso a la administración.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Tu usuario no tiene permisos de administración"
        )
    return _session_out(staff, True)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, config: RuntimeConfigDep) -> Response:
    if config.users.auth_mode == "ldap":
        request.session.clear()
    else:
        # Sin LDAP solo se cierra la administración: la sesión privada del chat continúa.
        request.session.pop(SESSION_ADMIN_KEY, None)
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
    body: PromptUpdate,
    admin: AdminDep,
    db: DbSession,
    settings: SettingsDep,
    config: RuntimeConfigDep,
) -> PromptOut:
    content = body.content.strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El prompt no puede estar vacío")
    if len(content) > config.chat.system_prompt_max_length:
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
    body: ChatConfigUpdate, admin: AdminDep, db: DbSession, config: RuntimeConfigDep
) -> ChatConfig:
    """Guarda el mensaje inicial y las preguntas sugeridas (los textos vacíos se descartan)."""
    welcome = (body.welcome_message or "").strip() or None
    if welcome and len(welcome) > config.chat.welcome_max_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "El mensaje inicial es demasiado largo"
        )
    questions = [q.strip() for q in body.suggested_questions if q.strip()]
    if any(len(q) > config.chat.suggested_question_max_length for q in questions):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Alguna pregunta sugerida es demasiado larga"
        )
    saved = await ChatConfigRepository(db).save(
        ChatConfig(welcome_message=welcome, suggested_questions=questions), admin.username
    )
    await db.commit()
    return saved


def _access_out(runtime: RuntimeConfig, config: AccessConfig) -> AccessOut:
    return AccessOut(
        mode=runtime.users.auth_mode,
        anonymous_allowed=config.anonymous_allowed,
        private_sessions=config.private_sessions,
    )


@router.get("/access", response_model=AccessOut)
async def get_access(_: AdminDep, db: DbSession, runtime: RuntimeConfigDep) -> AccessOut:
    return _access_out(runtime, await AccessRepository(db).get())


@router.put("/access", response_model=AccessOut)
async def update_access(
    body: AccessUpdate, admin: AdminDep, db: DbSession, runtime: RuntimeConfigDep
) -> AccessOut:
    """Con LDAP, permite o impide el acceso anónimo; sin LDAP, activa o desactiva las
    sesiones privadas. Al desactivarlos, las sesiones anónimas abiertas dejan de funcionar
    en su siguiente petición."""
    ldap = runtime.users.auth_mode == "ldap"
    if (body.anonymous_allowed is not None and not ldap) or (
        body.private_sessions is not None and ldap
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El acceso anónimo solo se configura con LDAP y las sesiones privadas sin LDAP",
        )
    repo = AccessRepository(db)
    config = await repo.get()
    if body.anonymous_allowed is not None:
        config = replace(config, anonymous_allowed=body.anonymous_allowed)
        change = ("Acceso anónimo", body.anonymous_allowed)
    elif body.private_sessions is not None:
        config = replace(config, private_sessions=body.private_sessions)
        change = ("Sesiones privadas", body.private_sessions)
    else:
        return _access_out(runtime, config)
    config = await repo.save(config, admin.username)
    await db.commit()
    logger.info(
        "%s %s",
        change[0],
        "activado" if change[1] else "desactivado",
        extra={"user_id": admin.username},
    )
    return _access_out(runtime, config)
