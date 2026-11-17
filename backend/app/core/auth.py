"""Identidad del usuario actual y acceso de administración.

De momento el chat es monousuario y anónimo. Todo el código obtiene el usuario a través
de `get_current_user` y comprueba la administración con `require_admin`, de modo que
añadir LDAP solo requiere sustituir estas dependencias.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings

ANONYMOUS_USER_ID = "anonymous"
SESSION_ADMIN_KEY = "admin"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Usuario autenticado (o anónimo)."""

    id: str
    display_name: str


ANONYMOUS_USER = CurrentUser(id=ANONYMOUS_USER_ID, display_name="Anónimo")


@dataclass(frozen=True, slots=True)
class AdminUser:
    """Administrador con sesión iniciada."""

    username: str


def get_current_user() -> CurrentUser:
    """Devuelve el usuario de la petición (fijo mientras no haya autenticación)."""
    return ANONYMOUS_USER


def verify_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    """Comprueba las credenciales de administración en tiempo constante."""
    if settings.admin_password is None:
        return False
    expected_password = settings.admin_password.get_secret_value().encode()
    user_ok = secrets.compare_digest(username.encode(), settings.admin_username.encode())
    password_ok = secrets.compare_digest(password.encode(), expected_password)
    return user_ok and password_ok


def get_admin(request: Request) -> AdminUser | None:
    """Administrador de la sesión actual, si lo hay."""
    username = request.session.get(SESSION_ADMIN_KEY)
    return AdminUser(username=username) if isinstance(username, str) else None


def require_admin(request: Request) -> AdminUser:
    """Exige una sesión de administración válida."""
    admin = get_admin(request)
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere iniciar sesión")
    return admin


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[AdminUser, Depends(require_admin)]
