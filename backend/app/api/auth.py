"""Sesión de los usuarios del chat: LDAP, acceso anónimo opcional y sesiones privadas."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep
from app.core.auth import (
    CurrentUser,
    active_session_user,
    end_session_user,
    new_anonymous_user,
    store_session_user,
)
from app.core.config import Settings
from app.core.ldap_auth import (
    DirectoryUnavailableError,
    InvalidCredentialsError,
    LdapAuthenticator,
)
from app.core.rate_limit import AnonymousSessionRateLimit, LoginRateLimit
from app.services.access import AccessRepository

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Pausa tras un intento fallido para dificultar ataques de fuerza bruta.
FAILED_LOGIN_DELAY_SECONDS = 0.5


class UserOut(BaseModel):
    id: str
    display_name: str
    is_admin: bool
    is_anonymous: bool = False


class AuthSessionOut(BaseModel):
    mode: Literal["none", "ldap"]
    authenticated: bool
    user: UserOut | None = None
    # Con LDAP: si la pantalla de inicio de sesión ofrece entrar de forma anónima.
    anonymous_allowed: bool = False
    # Sin LDAP: si cada visitante necesita su sesión privada (la crea el frontend al entrar).
    private_sessions: bool = False


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


async def _session_out(
    settings: Settings, db: DbSession, user: CurrentUser | None
) -> AuthSessionOut:
    access = await AccessRepository(db).get()
    if settings.auth_mode == "none" and not access.private_sessions:
        return AuthSessionOut(mode="none", authenticated=False)
    return AuthSessionOut(
        mode=settings.auth_mode,
        authenticated=user is not None,
        user=UserOut(
            id=user.id,
            display_name=user.display_name,
            is_admin=user.is_admin,
            is_anonymous=user.is_anonymous,
        )
        if user
        else None,
        anonymous_allowed=settings.auth_mode == "ldap" and access.anonymous_allowed,
        private_sessions=settings.auth_mode == "none",
    )


async def ldap_login(request: Request, username: str, password: str) -> CurrentUser:
    """Valida las credenciales en el directorio e inicia la sesión del usuario."""
    authenticator: LdapAuthenticator = request.app.state.ldap
    try:
        ldap_user = await authenticator.authenticate_async(username, password)
    except InvalidCredentialsError:
        logger.info("Inicio de sesión fallido", extra={"username": username[:255]})
        await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos"
        ) from None
    except DirectoryUnavailableError as exc:
        logger.error("No se puede consultar el directorio LDAP: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No se puede validar el usuario en este momento. Inténtalo más tarde.",
        ) from exc
    user = CurrentUser(
        id=ldap_user.id, display_name=ldap_user.display_name, is_admin=ldap_user.is_admin
    )
    store_session_user(request, user)
    logger.info("Inicio de sesión", extra={"user_id": user.id, "is_admin": user.is_admin})
    return user


@router.get("/session", response_model=AuthSessionOut)
async def get_session(request: Request, settings: SettingsDep, db: DbSession) -> AuthSessionOut:
    return await _session_out(settings, db, await active_session_user(request, settings, db))


@router.post("/session", response_model=AuthSessionOut)
async def login(
    body: UserLogin, request: Request, settings: SettingsDep, db: DbSession, _: LoginRateLimit
) -> AuthSessionOut:
    if settings.auth_mode != "ldap":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El inicio de sesión de usuarios no está activado"
        )
    user = await ldap_login(request, body.username, body.password)
    return await _session_out(settings, db, user)


@router.post("/anonymous", response_model=AuthSessionOut)
async def anonymous_login(
    request: Request, settings: SettingsDep, db: DbSession, _: AnonymousSessionRateLimit
) -> AuthSessionOut:
    """Inicia una sesión anónima nueva, si la administración lo permite.

    - Con LDAP: «Acceder de forma anónima» en la pantalla de inicio de sesión.
    - Sin LDAP, con sesiones privadas: la crea el frontend al entrar y al pulsar «Terminar
      sesión». Se conserva la sesión de administración de la cuenta local.

    Cada sesión es una identidad nueva: no se ven las conversaciones de sesiones anteriores
    ni las de otras personas. Las conversaciones se conservan para auditoría.
    """
    ldap = settings.auth_mode == "ldap"
    if not (await AccessRepository(db).get()).anonymous_sessions_enabled(settings.auth_mode):
        detail = (
            "El acceso anónimo no está permitido"
            if ldap
            else "Las sesiones privadas no están activadas"
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
    user = new_anonymous_user()
    store_session_user(request, user, keep_admin=not ldap)
    logger.info("Acceso anónimo" if ldap else "Sesión privada", extra={"user_id": user.id})
    return await _session_out(settings, db, user)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, settings: SettingsDep) -> Response:
    if settings.auth_mode == "ldap":
        request.session.clear()
    else:
        # Sin LDAP, la sesión de administración es independiente de la del chat.
        end_session_user(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
