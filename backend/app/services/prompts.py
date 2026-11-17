"""Gestión del prompt de sistema con histórico de versiones."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemPromptVersion


@dataclass(frozen=True, slots=True)
class CurrentPrompt:
    """Prompt vigente; `version` es `None` si se usa el predeterminado."""

    content: str
    version: SystemPromptVersion | None


class PromptRepository:
    """Lectura y escritura de versiones del prompt de sistema."""

    def __init__(self, session: AsyncSession, default_prompt: str) -> None:
        self._session = session
        self._default = default_prompt

    async def latest(self) -> SystemPromptVersion | None:
        return await self._session.scalar(
            select(SystemPromptVersion).order_by(SystemPromptVersion.id.desc()).limit(1)
        )

    async def current(self) -> CurrentPrompt:
        version = await self.latest()
        return CurrentPrompt(content=version.content if version else self._default, version=version)

    async def list_versions(self, limit: int = 50) -> list[SystemPromptVersion]:
        result = await self._session.scalars(
            select(SystemPromptVersion).order_by(SystemPromptVersion.id.desc()).limit(limit)
        )
        return list(result)

    async def get(self, version_id: int) -> SystemPromptVersion | None:
        return await self._session.get(SystemPromptVersion, version_id)

    async def save(self, content: str, author: str) -> SystemPromptVersion:
        """Guarda una nueva versión salvo que coincida con la vigente."""
        latest = await self.latest()
        if latest is not None and latest.content == content:
            return latest
        version = SystemPromptVersion(content=content, created_by=author)
        self._session.add(version)
        await self._session.flush()
        return version
