"""Configuración de la aplicación, leída de variables de entorno y de `.env`."""

import secrets
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
    # `text` para leer en consola; `json` (una línea por evento) para Docker y agregadores.
    log_format: Literal["text", "json"] = "text"
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

    # Administración. Sin contraseña configurada, la administración queda deshabilitada.
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
    # Clave para firmar la cookie de sesión. Si no se define, se genera una aleatoria
    # al arrancar (las sesiones se pierden al reiniciar).
    session_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)))
    session_max_age_seconds: int = Field(default=8 * 3600, gt=0)
    session_cookie_secure: bool = False
    system_prompt_max_length: int = Field(default=20_000, gt=0)

    # Límite de peticiones por IP y minuto (0 = sin límite). Se guarda en memoria: vale
    # para una única instancia del backend.
    rate_limit_chat_per_minute: int = Field(default=20, ge=0)
    rate_limit_login_per_minute: int = Field(default=5, ge=0)

    # Embeddings. `openai`: endpoint /v1/embeddings (p. ej. LiteLLM); `local`:
    # sentence-transformers (requiere `uv sync --extra local-embeddings`); `fake`: tests.
    embedding_provider: Literal["openai", "local", "fake"] = "openai"
    # Si no se indican, se usan la URL y la clave del modelo de lenguaje.
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "bge-m3"
    embedding_batch_size: int = Field(default=32, gt=0)

    # Documentos e indexación
    upload_max_mb: int = Field(default=50, gt=0)
    chunk_size_chars: int = Field(default=3000, ge=200)
    chunk_overlap_chars: int = Field(default=400, ge=0)

    # Recuperación (RAG)
    rag_enabled: bool = True
    rag_top_k: int = Field(default=5, ge=1, le=50)
    # Similitud coseno mínima (0-1) para usar un fragmento; depende del modelo de embeddings.
    rag_min_score: float = Field(default=0.3, ge=0, le=1)
    # Límite de caracteres de contexto enviados al modelo (protege su ventana de contexto).
    rag_max_context_chars: int = Field(default=12_000, ge=500)
    # Reescribe las preguntas de seguimiento como preguntas independientes antes de buscar
    # («¿y en agosto?» → «¿cuántos días de vacaciones hay en agosto?»). Cuesta una llamada
    # extra al modelo cuando hay historial.
    rag_condense_question: bool = True
    rag_condense_history_messages: int = Field(default=6, ge=1)

    # Conversación
    chat_history_max_messages: int = Field(default=20, ge=0)
    chat_max_message_length: int = Field(default=8000, gt=0)
    # Inicio del chat configurable desde la administración.
    chat_welcome_max_length: int = Field(default=2000, gt=0)
    chat_suggested_question_max_length: int = Field(default=300, gt=0)
    default_system_prompt: str = (
        "Eres un asistente útil. Responde siempre en español, de forma clara y concisa."
    )

    @property
    def uploads_dir(self) -> Path:
        """Carpeta donde se guardan los ficheros originales subidos."""
        return self.data_dir / "uploads"

    @property
    def vector_dir(self) -> Path:
        """Carpeta del índice vectorial."""
        return self.data_dir / "chroma"

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
