"""Ajustes de acceso al chat editables desde la administración (con LDAP)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, utcnow

ACCESS_KEY = "access"


@dataclass(frozen=True, slots=True)
class AccessConfig:
    """Con LDAP, si se permite usar el chat sin iniciar sesión (desactivado por defecto)."""

    anonymous_allowed: bool = False


class AccessRepository:
    """Lectura y escritura de los ajustes de acceso."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AccessConfig:
        setting = await self._session.get(AppSetting, ACCESS_KEY)
        if setting is None:
            return AccessConfig()
        return AccessConfig(anonymous_allowed=setting.value.get("anonymous_allowed") is True)

    async def save(self, config: AccessConfig, author: str) -> AccessConfig:
        value = {"anonymous_allowed": config.anonymous_allowed}
        setting = await self._session.get(AppSetting, ACCESS_KEY)
        if setting is None:
            self._session.add(AppSetting(key=ACCESS_KEY, value=value, updated_by=author))
        else:
            setting.value = value
            setting.updated_by = author
            setting.updated_at = utcnow()
        await self._session.flush()
        return config
