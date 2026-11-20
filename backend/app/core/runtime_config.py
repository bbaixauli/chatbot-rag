"""Configuración editable desde la administración (#42).

Los parámetros de funcionamiento (modelo de lenguaje, embeddings, troceado, recuperación,
conversación y límites de peticiones) se guardan en la base de datos y se cambian en caliente.
En el primer arranque se copian de las variables `CHATBOT_*` (ver `Settings`); a partir de
ahí, esas variables se ignoran. Lo necesario para arrancar o que depende del despliegue
(datos, sesión, administrador local, índice y cola) sigue en `.env`.

Cada grupo es un modelo inmutable; sus campos se llaman como la variable de `Settings` sin
el prefijo del grupo (`llm.model` ↔ `CHATBOT_LLM_MODEL`).
"""

from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import Settings


class ConfigGroup(BaseModel):
    """Grupo de parámetros que se guarda como una fila de `app_settings`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Prefijo del campo equivalente en `Settings` (y de la variable `CHATBOT_*`).
    SETTINGS_PREFIX: ClassVar[str] = ""
    # Excepciones al prefijo: campo del grupo → campo de `Settings`.
    SETTINGS_NAMES: ClassVar[dict[str, str]] = {}
    # Campos secretos: se cifran en la base de datos y la API nunca los devuelve.
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ()
    # Campos cuyo cambio obliga a reindexar los documentos.
    REINDEX_FIELDS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def settings_name(cls, field: str) -> str:
        return cls.SETTINGS_NAMES.get(field, f"{cls.SETTINGS_PREFIX}{field}")

    @classmethod
    def env_name(cls, field: str) -> str:
        return f"CHATBOT_{cls.settings_name(field).upper()}"

    @classmethod
    def values_from_settings(cls, settings: Settings) -> dict[str, Any]:
        return {field: getattr(settings, cls.settings_name(field)) for field in cls.model_fields}

    @classmethod
    def from_settings(cls, settings: Settings) -> "ConfigGroup":
        return cls.model_validate(cls.values_from_settings(settings))


class LlmConfig(ConfigGroup):
    """Modelo de lenguaje (cualquier servidor compatible con la API de OpenAI)."""

    SETTINGS_PREFIX = "llm_"
    SECRET_FIELDS = ("api_key",)

    provider: Literal["openai", "fake"]
    base_url: str = Field(min_length=1)
    api_key: SecretStr | None
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)


class EmbeddingConfig(ConfigGroup):
    """Embeddings. Sin URL ni clave propias se usan las del modelo de lenguaje."""

    SETTINGS_PREFIX = "embedding_"
    SECRET_FIELDS = ("api_key",)
    REINDEX_FIELDS = ("provider", "model", "base_url")

    provider: Literal["openai", "local", "fake"]
    base_url: str | None
    api_key: SecretStr | None
    model: str = Field(min_length=1)
    batch_size: int = Field(gt=0)


class IndexingConfig(ConfigGroup):
    """Subida y troceado de documentos."""

    REINDEX_FIELDS = ("chunk_size_chars", "chunk_overlap_chars")

    upload_max_mb: int = Field(gt=0)
    chunk_size_chars: int = Field(ge=200)
    chunk_overlap_chars: int = Field(ge=0)


class RagConfig(ConfigGroup):
    """Recuperación de fragmentos para cada pregunta."""

    SETTINGS_PREFIX = "rag_"

    enabled: bool
    top_k: int = Field(ge=1, le=50)
    # Similitud coseno mínima (0-1) para usar un fragmento; depende del modelo de embeddings.
    min_score: float = Field(ge=0, le=1)
    # Límite de caracteres de contexto enviados al modelo (protege su ventana de contexto).
    max_context_chars: int = Field(ge=500)
    # Reescribe las preguntas de seguimiento antes de buscar (una llamada extra al modelo).
    condense_question: bool
    condense_history_messages: int = Field(ge=1)


class ChatLimits(ConfigGroup):
    """Conversación y longitud de los textos."""

    SETTINGS_PREFIX = "chat_"
    SETTINGS_NAMES: ClassVar[dict[str, str]] = {
        "system_prompt_max_length": "system_prompt_max_length"
    }

    history_max_messages: int = Field(ge=0)
    max_message_length: int = Field(gt=0)
    welcome_max_length: int = Field(gt=0)
    suggested_question_max_length: int = Field(gt=0)
    system_prompt_max_length: int = Field(gt=0)


class RateLimits(ConfigGroup):
    """Peticiones por IP y minuto (0 = sin límite)."""

    SETTINGS_PREFIX = "rate_limit_"

    chat_per_minute: int = Field(ge=0)
    login_per_minute: int = Field(ge=0)


GroupName = Literal["llm", "embeddings", "indexing", "rag", "chat", "limits"]

GROUPS: dict[str, type[ConfigGroup]] = {
    "llm": LlmConfig,
    "embeddings": EmbeddingConfig,
    "indexing": IndexingConfig,
    "rag": RagConfig,
    "chat": ChatLimits,
    "limits": RateLimits,
}


class RuntimeConfig(BaseModel):
    """Configuración vigente: un valor por grupo."""

    model_config = ConfigDict(frozen=True)

    llm: LlmConfig
    embeddings: EmbeddingConfig
    indexing: IndexingConfig
    rag: RagConfig
    chat: ChatLimits
    limits: RateLimits

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeConfig":
        """Valores iniciales tomados de las variables `CHATBOT_*`."""
        return cls.model_validate(
            {name: group.values_from_settings(settings) for name, group in GROUPS.items()}
        )

    def group(self, name: str) -> ConfigGroup:
        value: ConfigGroup = getattr(self, name)
        return value

    def replace_group(self, name: str, value: ConfigGroup) -> "RuntimeConfig":
        return self.model_copy(update={name: value})


class ConfigSource(Protocol):
    """Origen de la configuración vigente (se consulta en cada uso)."""

    @property
    def current(self) -> RuntimeConfig: ...


class StaticConfig:
    """Configuración fija (tests y herramientas de línea de comandos)."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config

    @property
    def current(self) -> RuntimeConfig:
        return self._config

    @classmethod
    def from_settings(cls, settings: Settings) -> "StaticConfig":
        return cls(RuntimeConfig.from_settings(settings))
