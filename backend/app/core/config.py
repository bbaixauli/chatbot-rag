"""Configuración de la aplicación, leída de variables de entorno y de `.env`."""

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

APP_TITLE = "Chatbot RAG"

# Con una clave vacía o corta, cualquiera podría firmar cookies de sesión válidas
# (hacerse pasar por el administrador o por cualquier usuario).
SESSION_SECRET_MIN_LENGTH = 16
# Contraseña de ejemplo de `.env.example`: no se admite en producción.
EXAMPLE_ADMIN_PASSWORD = "cambia-esta-contraseña"  # noqa: S105 (solo para rechazarla)


def _random_secret() -> SecretStr:
    return SecretStr(secrets.token_urlsafe(32))


class Settings(BaseSettings):
    """Parámetros de la aplicación, leídos de variables `CHATBOT_*` (p. ej.
    `CHATBOT_DATA_DIR=/srv/chatbot/data`).

    Los marcados como «valores iniciales» se editan en la administración (ver
    `app.core.runtime_config`): las variables solo se usan para rellenarlos en el primer
    arranque. El código los lee de la configuración vigente, nunca de aquí.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHATBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    version: str = "0.1.0"
    environment: str = Field(default="development", pattern="^(development|test|production)$")
    log_level: str = "INFO"
    # Zona horaria para mostrar fechas y contar días en el cuadro de mando.
    timezone: str = "Europe/Madrid"
    # `text` para leer en consola; `json` (una línea por evento) para Docker y agregadores.
    log_format: Literal["text", "json"] = "text"
    data_dir: Path = Path("../data")
    # Por defecto, SQLite en `data_dir`. PostgreSQL: postgresql+asyncpg://usuario@servidor/bd
    database_url: str | None = None
    # Contraseña de la BD, aparte de la URL: sin codificar caracteres especiales y sin que
    # aparezca al mostrar la URL. Sustituye a la que lleve la URL.
    database_password: SecretStr | None = None

    # --- Valores iniciales (#42): modelo de lenguaje, servidor compatible con la API de OpenAI.
    # `fake` usa un modelo simulado sin red, útil para tests E2E y demos.
    llm_provider: Literal["openai", "fake"] = "openai"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_model: str = "llama3.1"
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    # Ventana de contexto del modelo de chat en el servidor (num_ctx en Ollama,
    # --max-model-len en vLLM) y lo que se reserva de ella para la respuesta (#73). Con la
    # ventana, el prompt se ajusta a ella; con 0 no se comprueba.
    llm_context_window_tokens: int = Field(default=0, ge=0)
    llm_response_reserve_tokens: int = Field(default=1024, ge=0)

    # Administración. Sin contraseña configurada, la administración queda deshabilitada.
    admin_username: str = "admin"
    admin_password: SecretStr | None = None
    # Clave para firmar la cookie de sesión. Si no se define (o está vacía), se genera una
    # aleatoria al arrancar (las sesiones se pierden al reiniciar); en producción es
    # obligatoria si hay sesiones (administración o LDAP).
    session_secret: SecretStr = Field(default_factory=_random_secret)
    session_max_age_seconds: int = Field(default=8 * 3600, gt=0)
    session_cookie_secure: bool = False

    # --- Valores iniciales (#42): usuarios del chat. `none` (anónimo, un único historial) o
    # `ldap` (cada usuario inicia sesión con su cuenta del directorio y ve solo las suyas).
    auth_mode: Literal["none", "ldap"] = "none"
    # ldaps://servidor:636 o ldap://servidor:389 (con ldap_start_tls para cifrar).
    ldap_url: str = "ldap://localhost:389"
    ldap_start_tls: bool = False
    # Fichero con el certificado de la CA del directorio (si no está en el almacén del
    # sistema). No es un valor inicial: sigue en `.env` y se lee en cada conexión; un
    # certificado pegado en la administración tiene prioridad.
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
    # Los miembros de este grupo ven la auditoría (vacío: la ven los administradores).
    ldap_auditor_group_dn: str | None = None
    # Solo Active Directory: tiene en cuenta grupos anidados.
    ldap_nested_groups: bool = False

    # --- Valores iniciales (#42): límite de peticiones por IP y minuto (0 = sin límite). Se
    # guarda en memoria: vale para una única instancia del backend.
    rate_limit_chat_per_minute: int = Field(default=20, ge=0)
    rate_limit_login_per_minute: int = Field(default=5, ge=0)

    # Auditoría (#50): revisión periódica de preguntas y respuestas y avisos por correo.
    # Valores iniciales: después se configuran en la administración.
    audit_enabled: bool = True
    audit_schedule: Literal["hourly", "daily"] = "daily"
    audit_daily_time: str = Field(default="02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    audit_use_llm: bool = True
    # Modelo para revisar (vacío: el del chat). Con LiteLLM puede ser uno de seguridad.
    audit_llm_model: str | None = None
    audit_max_per_run: int = Field(default=500, ge=1, le=10_000)
    # Antigüedad máxima (días) de lo que revisa el modelo; lo anterior, solo las reglas.
    audit_llm_history_days: int = Field(default=7, ge=0)
    # Pasados estos días se anonimizan usuario, IP y navegador (0 = nunca).
    audit_retention_days: int = Field(default=365, ge=0)
    audit_notify_enabled: bool = False
    audit_notify_min_severity: Literal["low", "medium", "high"] = "medium"
    # Destinatarios separados por comas.
    audit_notify_to: str = ""
    audit_smtp_host: str = ""
    audit_smtp_port: int = Field(default=587, ge=1, le=65535)
    audit_smtp_security: Literal["starttls", "ssl", "plain"] = "starttls"
    audit_smtp_username: str | None = None
    audit_smtp_password: SecretStr | None = None
    audit_smtp_from: str = ""

    # --- Valores iniciales (#42): embeddings. `openai`: endpoint /v1/embeddings (p. ej.
    # LiteLLM); `local`: sentence-transformers (requiere `uv sync --extra local-embeddings`);
    # `fake`: tests.
    embedding_provider: Literal["openai", "local", "fake"] = "openai"
    # Si no se indican, se usan la URL y la clave del modelo de lenguaje.
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "bge-m3"
    embedding_batch_size: int = Field(default=32, gt=0)

    # --- Valores iniciales (#42): documentos e indexación
    upload_max_mb: int = Field(default=50, gt=0)
    chunk_size_chars: int = Field(default=3000, ge=200)
    chunk_overlap_chars: int = Field(default=400, ge=0)
    # Preprocesado del texto antes de indexar (#62); cambiarlo obliga a reindexar.
    # Quita de los PDF las líneas repetidas en al menos esa proporción de páginas.
    indexing_strip_repeated_lines: bool = True
    indexing_repeated_line_min_ratio: float = Field(default=0.5, gt=0, le=1)
    # Sustituye los puntos de relleno de los índices por un espacio.
    indexing_clean_toc_leaders: bool = True
    # Antepone «Documento: <título>» y la sección al texto con el que se calcula el vector.
    indexing_context_prefix: bool = True
    # Detecta en los PDF los encabezados numerados («4.2 Altas») y los guarda como sección
    # de los fragmentos (#74).
    indexing_pdf_headings: bool = True

    # --- Valores iniciales: documentos web (#78). Tiempo máximo de cada descarga y
    # dominios de los que se pueden añadir documentos (separados por comas; cada uno admite
    # también sus subdominios). Vacío: cualquiera.
    web_timeout_seconds: int = Field(default=30, ge=1, le=300)
    web_allowed_domains: str = ""
    # Actualización periódica (#79): cada cuántas horas se comprueba si han cambiado y tras
    # cuántas comprobaciones seguidas con «no encontrado» (404 o 410) se borran.
    web_refresh_enabled: bool = True
    web_refresh_interval_hours: int = Field(default=24, ge=1, le=720)
    web_not_found_limit: int = Field(default=3, ge=1, le=100)

    # --- Valor inicial: conexiones HTTPS salientes (#89; documentos web, modelo de lenguaje y
    # embeddings). Exige que los certificados cumplan el RFC 5280 (p. ej. que tengan
    # Authority Key Identifier). Los certificados de confianza propios solo se configuran en
    # la administración.
    tls_strict_x509: bool = True

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

    # --- Valores iniciales (#42): recuperación (RAG)
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
    # Multiplica la puntuación de los fragmentos que parecen un índice del documento para
    # que no desplacen a los de contenido (1 = sin cambios; 0 = descartarlos).
    rag_toc_score_factor: float = Field(default=0.8, ge=0, le=1)
    # Puntuación mínima cuando la búsqueda se limita a unos documentos (#58): quien pregunta
    # ya ha indicado dónde está la respuesta, así que basta con el top-k.
    rag_scoped_min_score: float = Field(default=0.0, ge=0, le=1)
    # Si la pregunta nombra un documento (por su código, p. ej. «NOR-013», o su título), la
    # búsqueda se limita a él (#58).
    rag_detect_document_names: bool = True
    # Fragmentos anteriores y posteriores que se añaden a cada fragmento recuperado (0 = no
    # se añaden). Los contiguos se unen en un pasaje; siempre dentro de rag_max_context_chars.
    rag_neighbor_chunks: int = Field(default=0, ge=0, le=3)
    # Si la búsqueda se limita a un solo documento y su texto cabe en estos caracteres, se
    # envía el documento completo en lugar de fragmentos (0 = nunca). Debe caber en la
    # ventana de contexto del modelo de chat (unos 4 caracteres por token en español).
    rag_full_document_max_chars: int = Field(default=0, ge=0)
    # Si la pregunta cita un apartado («el apartado 4.2», «la sección de altas») y la búsqueda
    # está limitada a unos documentos, se añade el apartado completo (#74).
    rag_locate_sections: bool = True
    # Si falla la búsqueda en la documentación (embeddings o índice): `answer` responde sin ella
    # y lo avisa; `refuse` no responde y muestra un error (#97).
    rag_on_retrieval_failure: Literal["answer", "refuse"] = "answer"

    # --- Valores iniciales (#42): conversación
    chat_history_max_messages: int = Field(default=20, ge=0)
    chat_max_message_length: int = Field(default=8000, gt=0)
    # Inicio del chat configurable desde la administración.
    chat_welcome_max_length: int = Field(default=2000, gt=0)
    chat_suggested_question_max_length: int = Field(default=300, gt=0)
    system_prompt_max_length: int = Field(default=20_000, gt=0)
    # Prompt de sistema mientras no se guarde ninguno en la administración. Si se define la
    # variable, en el primer arranque se guarda como primera versión.
    default_system_prompt: str = (
        "Eres un asistente útil. Responde siempre en español, de forma clara y concisa."
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"CHATBOT_TIMEZONE no es una zona horaria válida: {self.timezone}"
            ) from exc
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
        self._check_session_security()
        return self

    @property
    def uses_sessions(self) -> bool:
        """True si hay inicio de sesión (administración o usuarios LDAP)."""
        return self.admin_password is not None or self.auth_mode == "ldap"

    @property
    def uses_example_admin_password(self) -> bool:
        """True si la contraseña de administración es la de `.env.example`."""
        password = self.admin_password
        return password is not None and password.get_secret_value() == EXAMPLE_ADMIN_PASSWORD

    def _check_session_security(self) -> None:
        production = self.environment == "production"
        secret = self.session_secret.get_secret_value().strip()
        if not secret:
            # `CHATBOT_SESSION_SECRET=` (vacía) cuenta como no definida: nunca se firma con
            # una clave vacía.
            if production and self.uses_sessions:
                raise ValueError(_missing_secret_message())
            self.session_secret = _random_secret()
        elif len(secret) < SESSION_SECRET_MIN_LENGTH:
            raise ValueError(
                f"CHATBOT_SESSION_SECRET debe tener al menos {SESSION_SECRET_MIN_LENGTH} "
                "caracteres. " + _GENERATE_KEY_HINT
            )
        elif production and self.uses_sessions and "session_secret" not in self.model_fields_set:
            raise ValueError(_missing_secret_message())
        if production and self.uses_example_admin_password:
            raise ValueError(
                "CHATBOT_ADMIN_PASSWORD tiene el valor de ejemplo de .env.example: cámbiala."
            )

    @property
    def zone(self) -> ZoneInfo:
        """Zona horaria de la aplicación (validada al arrancar)."""
        return ZoneInfo(self.timezone)

    @property
    def uploads_dir(self) -> Path:
        """Carpeta donde se guardan los ficheros originales subidos."""
        return self.data_dir / "uploads"

    @property
    def vector_dir(self) -> Path:
        """Carpeta del índice vectorial."""
        return self.data_dir / "chroma"

    @property
    def resolved_database_url(self) -> URL:
        """URL de la base de datos (por defecto, un SQLite dentro de `data_dir`) con la
        contraseña de `database_password`. Al convertirla en texto se oculta la contraseña."""
        url = make_url(
            self.database_url or f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').as_posix()}"
        )
        if self.database_password is not None:
            url = url.set(password=self.database_password.get_secret_value())
        return url


_GENERATE_KEY_HINT = 'Genera una con: python -c "import secrets; print(secrets.token_urlsafe(32))"'


def _missing_secret_message() -> str:
    return (
        "En producción, CHATBOT_SESSION_SECRET es obligatoria cuando hay inicio de sesión "
        "(administración o LDAP). " + _GENERATE_KEY_HINT
    )


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada para no releer el entorno en cada petición)."""
    return Settings()
