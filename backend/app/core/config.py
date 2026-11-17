"""Configuración de la aplicación, leída de variables de entorno y de `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parámetros configurables de la aplicación.

    Todas las variables se pueden sobrescribir con el prefijo `CHATBOT_`,
    por ejemplo `CHATBOT_DATA_DIR=/srv/chatbot/data`.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHATBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Chatbot RAG"
    version: str = "0.1.0"
    environment: str = Field(default="development", pattern="^(development|test|production)$")
    log_level: str = "INFO"
    data_dir: Path = Path("../data")
    database_url: str | None = None

    # Modelo de lenguaje (cualquier servidor compatible con la API de OpenAI).
    # `fake` usa un modelo simulado sin red, útil para tests E2E y demos.
    llm_provider: Literal["openai", "fake"] = "openai"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_model: str = "llama3.1"
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)

    # Conversación
    chat_history_max_messages: int = Field(default=20, ge=0)
    chat_max_message_length: int = Field(default=8000, gt=0)
    default_system_prompt: str = (
        "Eres un asistente útil. Responde siempre en español, de forma clara y concisa."
    )

    @property
    def resolved_database_url(self) -> str:
        """URL de la base de datos; por defecto un SQLite dentro de `data_dir`."""
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada para no releer el entorno en cada petición)."""
    return Settings()
