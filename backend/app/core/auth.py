"""Identidad del usuario actual y acceso de administración.

Dos modos (`CHATBOT_AUTH_MODE`):

- `none`: el chat es anónimo y todas las conversaciones pertenecen al usuario `anonymous`.
  La administración usa la cuenta local (`CHATBOT_ADMIN_USERNAME`/`CHATBOT_ADMIN_PASSWORD`).
- `ldap`: cada usuario inicia sesión con su cuenta del directorio y solo ve sus
  conversaciones. Son administradores los miembros de `CHATBOT_LDAP_ADMIN_GROUP_DN`; la
  cuenta local sigue disponible como acceso de emergencia si está configurada.

La identidad se guarda en la cookie de sesión firmada.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import SettingsDep
from app.core.config import Settings

ANONYMOUS_USER_ID = "anonymous"
SESSION_ADMIN_KEY = "admin"
SESSION_USER_KEY = "user"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Usuario autenticado (o anónimo)."""

    id: str
    display_name: str
    is_admin: bool = False


ANONYMOUS_USER = CurrentUser(id=ANONYMOUS_USER_ID, display_name="Anónimo")


@dataclass(frozen=True, slots=True)
class AdminUser:
    """Administrador con sesión iniciada."""

    username: str


def session_user(request: Request) -> CurrentUser | None:
    """Usuario del directorio guardado en la sesión, si lo hay."""
    data: Any = request.session.get(SESSION_USER_KEY)
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    return CurrentUser(
        id=data["id"],
        display_name=str(data.get("name") or data["id"]),
        is_admin=data.get("admin") is True,
    )


def store_session_user(request: Request, user: CurrentUser) -> None:
    """Inicia la sesión de un usuario (se descarta cualquier sesión previa)."""
    request.session.clear()
    request.session[SESSION_USER_KEY] = {
        "id": user.id,
        "name": user.display_name,
        "admin": user.is_admin,
    }


def get_current_user(request: Request, settings: SettingsDep) -> CurrentUser:
    """Usuario de la petición: anónimo sin autenticación, o el de la sesión con LDAP."""
    if settings.auth_mode == "none":
        return ANONYMOUS_USER
    user = session_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere iniciar sesión")
    return user


def verify_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    """Comprueba las credenciales de la cuenta local de administración en tiempo constante."""
    if settings.admin_password is None:
        return False
    expected_password = settings.admin_password.get_secret_value().encode()
    user_ok = secrets.compare_digest(username.encode(), settings.admin_username.encode())
    password_ok = secrets.compare_digest(password.encode(), expected_password)
    return user_ok and password_ok


def get_admin(request: Request) -> AdminUser | None:
    """Administrador de la sesión actual: cuenta local o usuario del grupo de administración."""
    username = request.session.get(SESSION_ADMIN_KEY)
    if isinstance(username, str):
        return AdminUser(username=username)
    user = session_user(request)
    if user is not None and user.is_admin:
        return AdminUser(username=user.id)
    return None


def require_admin(request: Request) -> AdminUser:
    """Exige una sesión de administración válida."""
    admin = get_admin(request)
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Se requiere iniciar sesión")
    return admin


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[AdminUser, Depends(require_admin)]
