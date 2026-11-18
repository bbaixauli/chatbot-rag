"""Configuración del inicio de las conversaciones: mensaje de bienvenida y preguntas sugeridas."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, utcnow

CHAT_CONFIG_KEY = "chat_welcome"
MAX_SUGGESTED_QUESTIONS = 5


@dataclass(frozen=True, slots=True)
class ChatConfig:
    """Mensaje inicial (opcional) y preguntas sugeridas al empezar una conversación."""

    welcome_message: str | None = None
    suggested_questions: list[str] = field(default_factory=list)


class ChatConfigRepository:
    """Lectura y escritura de la configuración del inicio del chat."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> ChatConfig:
        setting = await self._session.get(AppSetting, CHAT_CONFIG_KEY)
        if setting is None:
            return ChatConfig()
        value = setting.value
        questions = value.get("suggested_questions") or []
        return ChatConfig(
            welcome_message=value.get("welcome_message") or None,
            suggested_questions=[str(q) for q in questions][:MAX_SUGGESTED_QUESTIONS],
        )

    async def save(self, config: ChatConfig, author: str) -> ChatConfig:
        value = {
            "welcome_message": config.welcome_message,
            "suggested_questions": list(config.suggested_questions),
        }
        setting = await self._session.get(AppSetting, CHAT_CONFIG_KEY)
        if setting is None:
            self._session.add(AppSetting(key=CHAT_CONFIG_KEY, value=value, updated_by=author))
        else:
            setting.value = value
            setting.updated_by = author
            setting.updated_at = utcnow()
        await self._session.flush()
        return config
