"""Identidad del usuario actual y acceso de administración.

Dos modos (`CHATBOT_AUTH_MODE`):

- `none`: el chat es anónimo y todas las conversaciones pertenecen al usuario `anonymous`.
  La administración usa la cuenta local (`CHATBOT_ADMIN_USERNAME`/`CHATBOT_ADMIN_PASSWORD`).
  Si la administración activa las sesiones privadas, cada visitante recibe en su lugar una
  sesión anónima propia (`anon:<uuid>`), igual que el acceso anónimo con LDAP.
- `ldap`: cada usuario inicia sesión con su cuenta del directorio y solo ve sus
  conversaciones. Son administradores los miembros de `CHATBOT_LDAP_ADMIN_GROUP_DN`; la
  cuenta local sigue disponible como acceso de emergencia si está configurada. Si la
  administración lo permite, también se puede entrar de forma anónima: cada acceso recibe
  un id propio (`anon:<uuid>`), así que solo ve las conversaciones de esa sesión. Las
  conversaciones se conservan (auditoría) con ese id.

La identidad se guarda en la cookie de sesión firmada.
"""

import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import DbSession, RuntimeConfigDep
from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig
from app.services.access import AccessRepository

ANONYMOUS_USER_ID = "anonymous"
# Prefijo de los ids de las sesiones anónimas con LDAP (permite localizarlas en auditoría).
ANONYMOUS_SESSION_PREFIX = "anon:"
ANONYMOUS_DISPLAY_NAME = "Anónimo"
SESSION_ADMIN_KEY = "admin"
SESSION_USER_KEY = "user"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Usuario autenticado (o anónimo)."""

    id: str
    display_name: str
    is_admin: bool = False
    is_anonymous: bool = False


ANONYMOUS_USER = CurrentUser(id=ANONYMOUS_USER_ID, display_name=ANONYMOUS_DISPLAY_NAME)


def new_anonymous_user() -> CurrentUser:
    """Identidad de un acceso anónimo con LDAP: nueva en cada acceso."""
    return CurrentUser(
        id=f"{ANONYMOUS_SESSION_PREFIX}{uuid.uuid4().hex}",
        display_name=ANONYMOUS_DISPLAY_NAME,
        is_anonymous=True,
    )


@dataclass(frozen=True, slots=True)
class AdminUser:
    """Administrador con sesión iniciada."""

    username: str


def session_user(request: Request) -> CurrentUser | None:
    """Usuario del directorio guardado en la sesión, si lo hay."""
    data: Any = request.session.get(SESSION_USER_KEY)
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    anonymous = data.get("anonymous") is True
    if anonymous != data["id"].startswith(ANONYMOUS_SESSION_PREFIX):
        return None  # sesión incoherente: no se confía en ella
    return CurrentUser(
        id=data["id"],
        display_name=str(data.get("name") or data["id"]),
        # Un acceso anónimo nunca administra.
        is_admin=data.get("admin") is True and not anonymous,
        is_anonymous=anonymous,
    )


def store_session_user(request: Request, user: CurrentUser, keep_admin: bool = False) -> None:
    """Inicia la sesión de un usuario (se descarta cualquier sesión previa).

    Con `keep_admin`, se conserva la sesión de la cuenta local de administración: sin LDAP,
    el chat y la administración son sesiones independientes en la misma cookie.
    """
    admin = request.session.get(SESSION_ADMIN_KEY)
    request.session.clear()
    if keep_admin and isinstance(admin, str):
        request.session[SESSION_ADMIN_KEY] = admin
    request.session[SESSION_USER_KEY] = {
        "id": user.id,
        "name": user.display_name,
        "admin": user.is_admin,
        "anonymous": user.is_anonymous,
    }


def end_session_user(request: Request) -> None:
    """Cierra la sesión del chat. La de administración de la cuenta local se conserva."""
    request.session.pop(SESSION_USER_KEY, None)


async def active_session_user(
    request: Request, config: RuntimeConfig, db: DbSession
) -> CurrentUser | None:
    """Usuario de la sesión si sigue siendo válida.

    Una sesión anónima deja de valer en cuanto la administración desactiva el acceso
    anónimo (con LDAP) o las sesiones privadas (sin LDAP); se comprueba en cada petición y
    se descarta. Sin LDAP solo se admiten sesiones anónimas.
    """
    user = session_user(request)
    if user is None:
        return None
    auth_mode = config.users.auth_mode
    if auth_mode == "none" and not user.is_anonymous:
        return None  # p. ej. una sesión LDAP anterior a cambiar de modo
    if user.is_anonymous and not (await AccessRepository(db).get()).anonymous_sessions_enabled(
        auth_mode
    ):
        end_session_user(request)
        return None
    return user


async def get_current_user(
    request: Request, config: RuntimeConfigDep, db: DbSession
) -> CurrentUser:
    """Usuario de la petición.

    - LDAP: el de la sesión (del directorio o anónimo).
    - Sin LDAP: el historial compartido `anonymous` o, con las sesiones privadas activadas,
      la sesión privada del visitante.
    """
    auth_mode = config.users.auth_mode
    if auth_mode == "none" and not (await AccessRepository(db).get()).private_sessions:
        return ANONYMOUS_USER
    user = await active_session_user(request, config, db)
    if user is None:
        message = "Se requiere iniciar sesión" if auth_mode == "ldap" else "Sesión caducada"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, message)
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
