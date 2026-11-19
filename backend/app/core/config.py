"""Configuración de la aplicación, leída de variables de entorno y de `.env`."""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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

    # Usuarios del chat: `none` (anónimo, un único historial) o `ldap` (cada usuario inicia
    # sesión con su cuenta del directorio y ve solo sus conversaciones).
    auth_mode: Literal["none", "ldap"] = "none"
    # ldaps://servidor:636 o ldap://servidor:389 (con ldap_start_tls para cifrar).
    ldap_url: str = "ldap://localhost:389"
    ldap_start_tls: bool = False
    # Certificado de la CA del directorio (si no está en el almacén del sistema).
    ldap_ca_cert_file: Path | None = None
    ldap_timeout_seconds: float = Field(default=10, gt=0)
    # Cuenta de servicio para buscar usuarios (vacía = búsqueda anónima).
    ldap_bind_dn: str | None = None
    ldap_bind_password: SecretStr | None = None
    ldap_user_base_dn: str = ""
    # `{username}` se sustituye (escapado). Active Directory: (sAMAccountName={username})
    ldap_user_filter: str = "(uid={username})"
    ldap_user_id_attribute: str = "uid"
    ldap_display_name_attribute: str = "cn"
    # Los miembros de este grupo son administradores (vacío = solo la cuenta local).
    ldap_admin_group_dn: str | None = None
    # Solo Active Directory: tiene en cuenta grupos anidados.
    ldap_nested_groups: bool = False

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

    # Escalado (requiere `uv sync --extra escalado`). Por defecto, ChromaDB embebido y
    # tareas en el proceso de la API. Con `arq`, la indexación la hacen workers aparte
    # (`arq app.worker.WorkerSettings`) que leen la cola de Redis.
    vector_store: Literal["chroma", "qdrant"] = "chroma"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "chatbot_chunks"
    task_runner: Literal["inprocess", "arq"] = "inprocess"
    redis_url: str = "redis://localhost:6379/0"
    # Documentos que indexa a la vez cada proceso (API o worker).
    indexing_concurrency: int = Field(default=2, ge=1)

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

    @model_validator(mode="after")
    def _check_ldap(self) -> "Settings":
        if self.task_runner == "arq" and self.vector_store != "qdrant":
            # ChromaDB embebido vive en un único proceso: los workers no lo comparten.
            raise ValueError("CHATBOT_TASK_RUNNER=arq requiere CHATBOT_VECTOR_STORE=qdrant")
        if self.auth_mode == "ldap":
            if not self.ldap_user_base_dn:
                raise ValueError(
                    "CHATBOT_LDAP_USER_BASE_DN es obligatorio con CHATBOT_AUTH_MODE=ldap"
                )
            if "{username}" not in self.ldap_user_filter:
                raise ValueError("CHATBOT_LDAP_USER_FILTER debe contener {username}")
        return self

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
