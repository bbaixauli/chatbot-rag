"""Sesión de los usuarios del chat (modo LDAP)."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import SettingsDep
from app.core.auth import CurrentUser, session_user, store_session_user
from app.core.config import Settings
from app.core.ldap_auth import (
    DirectoryUnavailableError,
    InvalidCredentialsError,
    LdapAuthenticator,
)
from app.core.rate_limit import LoginRateLimit

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Pausa tras un intento fallido para dificultar ataques de fuerza bruta.
FAILED_LOGIN_DELAY_SECONDS = 0.5


class UserOut(BaseModel):
    id: str
    display_name: str
    is_admin: bool


class AuthSessionOut(BaseModel):
    mode: Literal["none", "ldap"]
    authenticated: bool
    user: UserOut | None = None


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


def _session_out(settings: Settings, user: CurrentUser | None) -> AuthSessionOut:
    if settings.auth_mode == "none":
        return AuthSessionOut(mode="none", authenticated=False)
    return AuthSessionOut(
        mode="ldap",
        authenticated=user is not None,
        user=UserOut(id=user.id, display_name=user.display_name, is_admin=user.is_admin)
        if user
        else None,
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
async def get_session(request: Request, settings: SettingsDep) -> AuthSessionOut:
    return _session_out(settings, session_user(request))


@router.post("/session", response_model=AuthSessionOut)
async def login(
    body: UserLogin, request: Request, settings: SettingsDep, _: LoginRateLimit
) -> AuthSessionOut:
    if settings.auth_mode != "ldap":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El inicio de sesión de usuarios no está activado"
        )
    user = await ldap_login(request, body.username, body.password)
    return _session_out(settings, user)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
