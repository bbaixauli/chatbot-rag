"""Almacén de la configuración editable desde la administración (#42).

Cada grupo de `RuntimeConfig` es una fila `config.<grupo>` de `app_settings`. Al arrancar la
API (`load(seed=True)`):

- Un grupo que no está en la base de datos se crea con los valores de `CHATBOT_*`.
- A un grupo guardado le pueden faltar campos (añadidos en versiones posteriores): se
  completan con los valores de `CHATBOT_*`.
- Una variable `CHATBOT_*` definida que no coincide con lo guardado se ignora, con un aviso
  en el log.
- Un secreto que no se puede descifrar (cambió `CHATBOT_SESSION_SECRET`) se toma de
  `CHATBOT_*` hasta que se vuelva a guardar, y la administración lo indica.

La API guarda en memoria la configuración vigente y la actualiza al guardar; los workers de
la cola la vuelven a leer antes de cada trabajo.
"""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_config import GROUPS, ConfigGroup, RuntimeConfig
from app.core.secrets import SecretCipher, SecretUnreadableError
from app.db.models import AppSetting, SystemPromptVersion, utcnow
from app.db.session import DATABASE_NOT_READY, build_engine, build_sessionmaker

logger = logging.getLogger(__name__)

KEY_PREFIX = "config."
# Autor de los valores copiados de las variables de entorno.
ENV_AUTHOR = "env"


def setting_key(group: str) -> str:
    return f"{KEY_PREFIX}{group}"


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """Quién y cuándo guardó un grupo (`env` y sin fecha si aún no está en la BD)."""

    updated_by: str
    updated_at: datetime | None


class ConfigStore:
    """Configuración vigente, persistida en `app_settings`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._cipher = SecretCipher(settings.session_secret)
        self._current = RuntimeConfig.from_settings(settings)
        self._info = {name: GroupInfo(ENV_AUTHOR, None) for name in GROUPS}
        self._unreadable: frozenset[str] = frozenset()
        self._lock = asyncio.Lock()

    @property
    def current(self) -> RuntimeConfig:
        return self._current

    def info(self, group: str) -> GroupInfo:
        return self._info[group]

    @property
    def unreadable_secrets(self) -> frozenset[str]:
        """Secretos guardados que no se pueden descifrar, como `grupo.campo`."""
        return self._unreadable

    async def load(self, *, seed: bool) -> None:
        """Lee la configuración de la BD; con `seed`, guarda los grupos que falten."""
        raced = False
        async with self._lock, self._sessionmaker() as session:
            rows = {
                row.key: row
                for row in await session.scalars(
                    select(AppSetting).where(AppSetting.key.in_(map(setting_key, GROUPS)))
                )
            }
            groups: dict[str, ConfigGroup] = {}
            info: dict[str, GroupInfo] = {}
            unreadable: set[str] = set()
            for name, cls in GROUPS.items():
                row = rows.get(setting_key(name))
                if row is None:
                    group = cls.from_settings(self._settings)
                    info[name] = GroupInfo(ENV_AUTHOR, None)
                    if seed:
                        now = utcnow()
                        session.add(
                            AppSetting(
                                key=setting_key(name),
                                value=self._dump(group),
                                updated_by=ENV_AUTHOR,
                                updated_at=now,
                            )
                        )
                        info[name] = GroupInfo(ENV_AUTHOR, now)
                        logger.info("Configuración «%s» copiada de las variables CHATBOT_*", name)
                else:
                    group = self._parse(name, cls, row.value, unreadable)
                    info[name] = GroupInfo(row.updated_by, row.updated_at)
                    if seed:
                        self._warn_ignored_env(name, group, unreadable)
                groups[name] = group
            if seed:
                await self._seed_system_prompt(session)
                try:
                    await session.commit()
                except IntegrityError:
                    # Otro proceso sembró a la vez: se lee lo que guardó.
                    await session.rollback()
                    raced = True
        if raced:
            await self.load(seed=False)
            return
        # Solo se avisa de los nuevos: los workers releen la configuración en cada trabajo.
        new_unreadable = unreadable - self._unreadable
        self._current = RuntimeConfig.model_validate(groups)
        self._info = info
        self._unreadable = frozenset(unreadable)
        for secret in sorted(new_unreadable):
            logger.warning(
                "No se puede descifrar %s (¿ha cambiado CHATBOT_SESSION_SECRET?): se usa el "
                "valor de las variables de entorno. Vuelve a introducirlo en la administración.",
                secret,
            )

    async def reload(self) -> None:
        """Vuelve a leer la configuración (p. ej. un worker antes de cada trabajo)."""
        try:
            await self.load(seed=False)
        except DATABASE_NOT_READY:
            logger.warning("No se pudo leer la configuración; se mantiene la anterior")

    async def save(self, name: str, value: ConfigGroup, author: str) -> None:
        """Guarda un grupo y lo aplica de inmediato."""
        async with self._lock, self._sessionmaker() as session:
            row = await session.get(AppSetting, setting_key(name))
            now = utcnow()
            if row is None:
                session.add(
                    AppSetting(
                        key=setting_key(name),
                        value=self._dump(value),
                        updated_by=author,
                        updated_at=now,
                    )
                )
            else:
                row.value = self._dump(value)
                row.updated_by = author
                row.updated_at = now
            await session.commit()
            self._current = self._current.replace_group(name, value)
            self._info = {**self._info, name: GroupInfo(author, now)}
            # Al guardar se vuelve a cifrar con la clave actual.
            self._unreadable = frozenset(
                s for s in self._unreadable if not s.startswith(f"{name}.")
            )

    async def reset(self, names: Iterable[str]) -> None:
        """Descarta lo guardado de esos grupos y los vuelve a copiar de `CHATBOT_*`."""
        keys = [setting_key(name) for name in names]
        async with self._sessionmaker() as session:
            await session.execute(delete(AppSetting).where(AppSetting.key.in_(keys)))
            await session.commit()
        await self.load(seed=True)

    def _dump(self, group: ConfigGroup) -> dict[str, Any]:
        data: dict[str, Any] = group.model_dump(mode="json", exclude=set(group.SECRET_FIELDS))
        for field in group.SECRET_FIELDS:
            secret: SecretStr | None = getattr(group, field)
            data[field] = (
                None
                if secret is None
                else {"encrypted": self._cipher.encrypt(secret.get_secret_value())}
            )
        return data

    def _parse(
        self, name: str, cls: type[ConfigGroup], stored: Any, unreadable: set[str]
    ) -> ConfigGroup:
        values = cls.values_from_settings(self._settings)
        if isinstance(stored, dict):
            for field, raw in stored.items():
                if field not in cls.model_fields:
                    continue  # campo retirado en una versión posterior
                if field in cls.SECRET_FIELDS:
                    if raw is None:
                        values[field] = None
                    elif isinstance(raw, dict) and isinstance(raw.get("encrypted"), str):
                        try:
                            values[field] = SecretStr(self._cipher.decrypt(raw["encrypted"]))
                        except SecretUnreadableError:
                            unreadable.add(f"{name}.{field}")
                    continue
                values[field] = raw
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            logger.error(
                "La configuración «%s» guardada no es válida; se usan las variables CHATBOT_*: %s",
                name,
                exc,
            )
            return cls.from_settings(self._settings)

    def _warn_ignored_env(self, name: str, group: ConfigGroup, unreadable: set[str]) -> None:
        explicit = self._settings.model_fields_set
        from_env = type(group).values_from_settings(self._settings)
        for field in type(group).model_fields:
            if f"{name}.{field}" in unreadable:
                continue
            if group.settings_name(field) in explicit and from_env[field] != getattr(group, field):
                logger.warning(
                    "%s se ignora: el valor vigente se configura en la administración",
                    group.env_name(field),
                )

    async def _seed_system_prompt(self, session: AsyncSession) -> None:
        """`CHATBOT_DEFAULT_SYSTEM_PROMPT` pasa a ser la primera versión del prompt."""
        if "default_system_prompt" not in self._settings.model_fields_set:
            return
        if await session.scalar(select(SystemPromptVersion.id).limit(1)) is not None:
            return
        session.add(
            SystemPromptVersion(content=self._settings.default_system_prompt, created_by=ENV_AUTHOR)
        )


async def load_runtime_config(settings: Settings) -> RuntimeConfig:
    """Configuración vigente para las herramientas de línea de comandos.

    Si la base de datos no existe o no está migrada, se usan las variables `CHATBOT_*`.
    """
    engine = build_engine(settings.resolved_database_url)
    try:
        store = ConfigStore(build_sessionmaker(engine), settings)
        try:
            await store.load(seed=False)
        except DATABASE_NOT_READY:
            logger.warning("No se pudo leer la configuración de la BD: se usa la de CHATBOT_*")
        return store.current
    finally:
        await engine.dispose()
