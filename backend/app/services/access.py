"""Ajustes de acceso al chat editables desde la administración.

- Con LDAP, `anonymous_allowed`: se puede entrar sin cuenta (#30).
- Sin LDAP, `private_sessions`: cada visitante tiene una sesión privada y temporal en lugar
  del historial compartido (#33).

En ambos casos la identidad es una sesión anónima `anon:<uuid>` (ver `app.core.auth`).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, utcnow

ACCESS_KEY = "access"


@dataclass(frozen=True, slots=True)
class AccessConfig:
    """Ajustes de acceso (todos desactivados por defecto)."""

    anonymous_allowed: bool = False
    private_sessions: bool = False

    def anonymous_sessions_enabled(self, auth_mode: str) -> bool:
        """Si valen las sesiones anónimas en el modo de autenticación indicado."""
        return self.anonymous_allowed if auth_mode == "ldap" else self.private_sessions


class AccessRepository:
    """Lectura y escritura de los ajustes de acceso."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AccessConfig:
        setting = await self._session.get(AppSetting, ACCESS_KEY)
        if setting is None:
            return AccessConfig()
        value = setting.value
        return AccessConfig(
            anonymous_allowed=value.get("anonymous_allowed") is True,
            private_sessions=value.get("private_sessions") is True,
        )

    async def save(self, config: AccessConfig, author: str) -> AccessConfig:
        value = {
            "anonymous_allowed": config.anonymous_allowed,
            "private_sessions": config.private_sessions,
        }
        setting = await self._session.get(AppSetting, ACCESS_KEY)
        if setting is None:
            self._session.add(AppSetting(key=ACCESS_KEY, value=value, updated_by=author))
        else:
            setting.value = value
            setting.updated_by = author
            setting.updated_at = utcnow()
        await self._session.flush()
        return config
