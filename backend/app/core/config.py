"""Configuración de la aplicación, leída de variables de entorno y de `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
