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

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.core.config import Settings
from app.core.tls import parse_certificates

# Los nombres de modelo se guardan en columnas de 255 caracteres (registro de preguntas,
# documentos, auditoría).
MODEL_NAME_MAX_LENGTH = 255


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
    # Campos sin variable de entorno equivalente (solo se configuran en la administración).
    ADMIN_ONLY_FIELDS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def settings_name(cls, field: str) -> str:
        return cls.SETTINGS_NAMES.get(field, f"{cls.SETTINGS_PREFIX}{field}")

    @classmethod
    def env_name(cls, field: str) -> str:
        return f"CHATBOT_{cls.settings_name(field).upper()}"

    @classmethod
    def values_from_settings(cls, settings: Settings) -> dict[str, Any]:
        return {
            field: None
            if field in cls.ADMIN_ONLY_FIELDS
            else getattr(settings, cls.settings_name(field))
            for field in cls.model_fields
        }

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
    model: str = Field(min_length=1, max_length=MODEL_NAME_MAX_LENGTH)
    temperature: float = Field(ge=0, le=2)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    # Ventana de contexto del modelo, tal como está configurada en el servidor (#73); 0: no
    # se comprueba el tamaño del prompt. Ver `services/prompt_budget.py`.
    context_window_tokens: int = Field(ge=0)
    # Parte de la ventana que se deja para la respuesta.
    response_reserve_tokens: int = Field(ge=0)

    @property
    def prompt_max_tokens(self) -> int | None:
        """Tokens que puede ocupar el prompt, o `None` si no se comprueba."""
        if not self.context_window_tokens:
            return None
        return self.context_window_tokens - self.response_reserve_tokens

    @model_validator(mode="after")
    def _check_window(self) -> "LlmConfig":
        if (
            self.context_window_tokens
            and self.response_reserve_tokens >= self.context_window_tokens
        ):
            raise ValueError(
                "El espacio reservado para la respuesta debe ser menor que la ventana de contexto"
            )
        return self


class EmbeddingConfig(ConfigGroup):
    """Embeddings. Sin URL ni clave propias se usan las del modelo de lenguaje."""

    SETTINGS_PREFIX = "embedding_"
    SECRET_FIELDS = ("api_key",)
    REINDEX_FIELDS = ("provider", "model", "base_url")

    provider: Literal["openai", "local", "fake"]
    base_url: str | None
    api_key: SecretStr | None
    model: str = Field(min_length=1, max_length=MODEL_NAME_MAX_LENGTH)
    batch_size: int = Field(gt=0)


class IndexingConfig(ConfigGroup):
    """Subida y troceado de documentos."""

    SETTINGS_NAMES: ClassVar[dict[str, str]] = {
        "strip_repeated_lines": "indexing_strip_repeated_lines",
        "repeated_line_min_ratio": "indexing_repeated_line_min_ratio",
        "clean_toc_leaders": "indexing_clean_toc_leaders",
        "context_prefix": "indexing_context_prefix",
    }
    REINDEX_FIELDS = (
        "chunk_size_chars",
        "chunk_overlap_chars",
        "strip_repeated_lines",
        "repeated_line_min_ratio",
        "clean_toc_leaders",
        "context_prefix",
    )

    upload_max_mb: int = Field(gt=0)
    chunk_size_chars: int = Field(ge=200)
    chunk_overlap_chars: int = Field(ge=0)
    # Preprocesado (#62): ver `services/ingestion/preprocess.py`.
    # Quita de los PDF las líneas que se repiten en muchas páginas (sellos, cabeceras, pies).
    strip_repeated_lines: bool
    # Proporción de páginas (0-1) en las que debe aparecer una línea para quitarla.
    repeated_line_min_ratio: float = Field(gt=0, le=1)
    # Sustituye los puntos de relleno de los índices («Objetivo ...... 5») por un espacio.
    clean_toc_leaders: bool
    # Antepone el título del documento y la sección al calcular el vector de cada fragmento.
    context_prefix: bool


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
    # Multiplica la puntuación de los fragmentos de índice (tabla de contenidos); 1 = igual.
    toc_score_factor: float = Field(ge=0, le=1)
    # Puntuación mínima cuando la búsqueda se limita a unos documentos.
    scoped_min_score: float = Field(ge=0, le=1)
    # Limita la búsqueda a los documentos que se nombran en la pregunta (código o título).
    detect_document_names: bool
    # Fragmentos vecinos (anteriores y posteriores) que se añaden a cada fragmento recuperado.
    neighbor_chunks: int = Field(ge=0, le=3)
    # Documento completo si la búsqueda se limita a uno que cabe en estos caracteres (0: no).
    full_document_max_chars: int = Field(ge=0)


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


class UsersConfig(ConfigGroup):
    """Usuarios del chat: anónimos (`none`) o con cuenta del directorio (`ldap`).

    La cuenta local de administración (`CHATBOT_ADMIN_*`) sigue en `.env`: es el acceso de
    rescate si el directorio falla o está mal configurado.
    """

    SECRET_FIELDS = ("ldap_bind_password",)
    # El certificado de la CA también puede estar en un fichero del despliegue
    # (CHATBOT_LDAP_CA_CERT_FILE, en `.env`, que se lee en cada conexión); el de aquí tiene
    # prioridad.
    ADMIN_ONLY_FIELDS = ("ldap_ca_cert",)
    # Campos que determinan la conexión con el directorio (se prueban antes de guardar).
    CONNECTION_FIELDS: ClassVar[tuple[str, ...]] = (
        "ldap_url",
        "ldap_start_tls",
        "ldap_ca_cert",
        "ldap_timeout_seconds",
        "ldap_bind_dn",
        "ldap_bind_password",
        "ldap_user_base_dn",
    )

    auth_mode: Literal["none", "ldap"]
    # ldaps://servidor:636 o ldap://servidor:389 (con ldap_start_tls para cifrar).
    ldap_url: str = Field(min_length=1)
    ldap_start_tls: bool
    # Certificado (PEM) de la CA del directorio, si no está en el almacén del sistema.
    ldap_ca_cert: str | None = None
    ldap_timeout_seconds: float = Field(gt=0)
    # Cuenta de servicio para buscar usuarios (vacía = búsqueda anónima).
    ldap_bind_dn: str | None
    ldap_bind_password: SecretStr | None
    ldap_user_base_dn: str
    # `{username}` se sustituye (escapado). Active Directory: (sAMAccountName={username})
    ldap_user_filter: str = Field(min_length=1)
    ldap_user_id_attribute: str = Field(min_length=1)
    ldap_display_name_attribute: str = Field(min_length=1)
    # Los miembros de este grupo son administradores (vacío = solo la cuenta local).
    ldap_admin_group_dn: str | None
    # Los miembros de este grupo ven la auditoría (#50). Vacío: la ven los
    # administradores. Si se indica, solo sus miembros.
    ldap_auditor_group_dn: str | None = None
    # Solo Active Directory: tiene en cuenta grupos anidados.
    ldap_nested_groups: bool

    @model_validator(mode="after")
    def _check_ldap(self) -> "UsersConfig":
        if self.auth_mode == "ldap":
            if not self.ldap_user_base_dn.strip():
                raise ValueError("Con LDAP hay que indicar la base de búsqueda de usuarios")
            if "{username}" not in self.ldap_user_filter:
                raise ValueError("El filtro de usuarios debe contener {username}")
        return self


class AuditConfig(ConfigGroup):
    """Auditoría (#50): revisión periódica de preguntas y respuestas y avisos por correo."""

    SETTINGS_PREFIX = "audit_"
    SECRET_FIELDS = ("smtp_password",)

    enabled: bool
    schedule: Literal["hourly", "daily"]
    daily_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    use_llm: bool
    llm_model: str | None = Field(max_length=MODEL_NAME_MAX_LENGTH)
    max_per_run: int = Field(ge=1, le=10_000)
    llm_history_days: int = Field(ge=0)
    retention_days: int = Field(ge=0)
    notify_enabled: bool
    notify_min_severity: Literal["low", "medium", "high"]
    notify_to: str | None
    smtp_host: str | None
    smtp_port: int = Field(ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "plain"]
    smtp_username: str | None
    smtp_password: SecretStr | None
    smtp_from: str | None

    @property
    def recipients(self) -> list[str]:
        return [a.strip() for a in (self.notify_to or "").split(",") if a.strip()]

    @model_validator(mode="after")
    def _check_notifications(self) -> "AuditConfig":
        if self.notify_enabled:
            if not (self.smtp_host or "").strip():
                raise ValueError("Para enviar avisos por correo hay que indicar el servidor SMTP")
            if "@" not in (self.smtp_from or ""):
                raise ValueError("Para enviar avisos por correo hay que indicar el remitente")
            if not self.recipients or any("@" not in a for a in self.recipients):
                raise ValueError("Indica uno o varios destinatarios válidos, separados por comas")
        return self


class WebConfig(ConfigGroup):
    """Documentos añadidos por su dirección web (#78)."""

    SETTINGS_PREFIX = "web_"

    timeout_seconds: int = Field(ge=1, le=300)
    # Dominios permitidos separados por comas (cada uno con sus subdominios); vacío: todos.
    allowed_domains: str | None
    # Actualización periódica (#79).
    refresh_enabled: bool
    refresh_interval_hours: int = Field(ge=1, le=720)
    # Comprobaciones seguidas con «no encontrado» (404 o 410) tras las que se borra.
    not_found_limit: int = Field(ge=1, le=100)

    @property
    def domains(self) -> list[str]:
        """Dominios permitidos, en minúsculas y sin el punto inicial («.empresa.es»)."""
        return [
            d.strip().lower().lstrip(".")
            for d in (self.allowed_domains or "").split(",")
            if d.strip().strip(".")
        ]


class TlsConfig(ConfigGroup):
    """Conexiones HTTPS salientes (#89): documentos web, modelo de lenguaje y embeddings."""

    SETTINGS_PREFIX = "tls_"
    ADMIN_ONLY_FIELDS = ("ca_certs",)

    # Certificados (PEM, uno o varios) de CA propias, que se suman a las públicas.
    ca_certs: str | None = None
    # Comprobación X.509 estricta (RFC 5280): ver `core/tls.py`.
    strict_x509: bool

    @field_validator("ca_certs")
    @classmethod
    def _check_certificates(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        parse_certificates(text)
        return text


GroupName = Literal[
    "llm", "embeddings", "indexing", "rag", "chat", "limits", "users", "audit", "web", "tls"
]

GROUPS: dict[str, type[ConfigGroup]] = {
    "llm": LlmConfig,
    "embeddings": EmbeddingConfig,
    "indexing": IndexingConfig,
    "rag": RagConfig,
    "chat": ChatLimits,
    "limits": RateLimits,
    "users": UsersConfig,
    "audit": AuditConfig,
    "web": WebConfig,
    "tls": TlsConfig,
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
    users: UsersConfig
    audit: AuditConfig
    web: WebConfig
    tls: TlsConfig

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
