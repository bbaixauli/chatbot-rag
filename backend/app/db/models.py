"""Modelos ORM de la aplicación."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Fecha y hora actual en UTC."""
    return datetime.now(UTC)


class MessageRole(StrEnum):
    """Autor de un mensaje."""

    USER = "user"
    ASSISTANT = "assistant"


class ScopeSource(StrEnum):
    """Origen de la limitación de la búsqueda a unos documentos (#58)."""

    SELECTOR = "selector"  # elegidos en el chat
    DETECTED = "detected"  # nombrados en la pregunta


class Conversation(Base):
    """Conversación del chat."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Nulo/`anonymous` mientras no haya autenticación; preparado para LDAP.
    user_id: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Documentos a los que se limita la búsqueda (elegidos en el chat, #58); nulo: todos.
    document_ids: Mapped[list[str] | None] = mapped_column(JSON)
    # Categorías elegidas (#85): se busca en sus documentos actuales; nulo: ninguna.
    category_ids: Mapped[list[int] | None] = mapped_column(JSON)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )


class DocumentStatus(StrEnum):
    """Estado de procesamiento de un documento."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class DocumentKind(StrEnum):
    """Formatos admitidos."""

    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"
    # Office y OpenDocument (#76).
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    ODT = "odt"
    ODP = "odp"
    ODS = "ods"
    # Página web añadida por su dirección (#78): se guarda su contenido principal, ya limpio,
    # como Markdown.
    WEB = "web"


class Category(Base):
    """Categoría para organizar los documentos (un solo nivel, #75)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """Fichero subido para el RAG."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(255))
    # Lo que se muestra en las referencias y se pasa al indexar (#75). Por defecto, el de
    # los metadatos del fichero; si no tiene uno válido, el nombre del fichero.
    title: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=DocumentStatus.PENDING.value)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    # Troceado con el que se indexó (para avisar si cambia la configuración).
    chunk_size_chars: Mapped[int | None] = mapped_column(Integer)
    chunk_overlap_chars: Mapped[int | None] = mapped_column(Integer)
    # Preprocesado con el que se indexó (`preprocessing_signature`, #62).
    preprocessing: Mapped[str | None] = mapped_column(String(64))
    # Título y categoría con los que se indexó (van en el texto de los vectores): si
    # cambian, el documento queda desactualizado.
    indexed_title: Mapped[str | None] = mapped_column(String(255))
    indexed_category: Mapped[str | None] = mapped_column(String(100))
    uploaded_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Documentos añadidos por su dirección web (#78): la dirección (a la que enlazan las
    # referencias) y las cabeceras de la última descarga (para actualizarlo, #79).
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_etag: Mapped[str | None] = mapped_column(String(255))
    source_last_modified: Mapped[str | None] = mapped_column(String(64))
    # Actualización periódica (#79): última comprobación, último cambio, último error de la
    # comprobación y comprobaciones seguidas con «no encontrado».
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_error: Mapped[str | None] = mapped_column(Text)
    source_not_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SystemPromptVersion(Base):
    """Versión del prompt de sistema. La vigente es la más reciente."""

    __tablename__ = "system_prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """Ajuste de la aplicación editable desde la administración (clave → valor JSON)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    """Mensaje dentro de una conversación."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)

    # Entero autoincremental: garantiza el orden de los mensajes.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Respuestas: documentos a los que se limitó la búsqueda (#58), como el evento `scope`:
    # {"documents": [{"id", "filename", "title"}], "source": "selector" | "detected"}.
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Respuestas: falló la búsqueda en la documentación (embeddings o índice, #95).
    retrieval_failed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sources: Mapped[list["MessageSource"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MessageSource.number",
        lazy="selectin",
    )


class MessageSource(Base):
    """Fragmento de documento usado para generar una respuesta."""

    __tablename__ = "message_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Si se elimina el documento se conserva la referencia (sin enlace).
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    # Número de la cita en la respuesta: [1], [2]…
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    page: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    snippet: Mapped[str] = mapped_column(Text)
    # True si el modelo la citó; False si solo se consultó (el modelo no citó nada).
    cited: Mapped[bool] = mapped_column(Boolean, default=True)

    message: Mapped[Message] = relationship(back_populates="sources")


class QuestionStatus(StrEnum):
    """Resultado de una pregunta en el registro del cuadro de mando."""

    OK = "ok"  # el modelo respondió
    ERROR = "error"  # el modelo falló o devolvió una respuesta vacía
    CANCELLED = "cancelled"  # la persona cerró la conexión antes de terminar


class AccessKind(StrEnum):
    """Cómo accedió quien preguntó (para las estadísticas; no identifica a nadie)."""

    LDAP = "ldap"
    ANONYMOUS = "anonymous"  # acceso anónimo con LDAP
    PRIVATE = "private"  # sesión privada sin LDAP
    SHARED = "shared"  # historial compartido sin LDAP


class QuestionLog(Base):
    """Registro de cada pregunta para el cuadro de mando (#51) y la auditoría (#50).

    Es independiente de las conversaciones: si se borra la conversación, la fila y sus
    textos se conservan (`conversation_id` pasa a nulo).
    """

    __tablename__ = "question_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(255), index=True)
    access_kind: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    # La respuesta dice que no tiene la información (frases configurables del cuadro de mando).
    no_info: Mapped[bool] = mapped_column(Boolean, default=False)
    # Desde que llega la pregunta hasta el primer fragmento y hasta el último (milisegundos).
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    response_ms: Mapped[int | None] = mapped_column(Integer)
    sources_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    sources_cited: Mapped[int] = mapped_column(Integer, default=0)
    # Falló la búsqueda en la documentación (#95): distingue «no se pudo buscar» de «no había
    # nada relevante» (los dos con `sources_retrieved` a 0).
    retrieval_failed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Auditoría (#50): quién, desde dónde y con qué se respondió. La IP, el navegador y el
    # nombre se anonimizan pasado el plazo configurado.
    client_ip: Mapped[str | None] = mapped_column(String(45), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user_display_name: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version_id: Mapped[int | None] = mapped_column(Integer)
    # Consulta con la que se buscó en los documentos (la pregunta reformulada).
    search_query: Mapped[str | None] = mapped_column(Text)
    # [{"document_id", "filename", "title", "page", "cited"}] de las fuentes mostradas.
    cited_documents: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Búsqueda limitada a unos documentos (#58): cuáles y por qué (`ScopeSource`).
    scope_document_ids: Mapped[list[str] | None] = mapped_column(JSON)
    scope_source: Mapped[str | None] = mapped_column(String(16))
    # Categorías elegidas en el chat (#85); sus documentos están en `scope_document_ids`.
    scope_category_ids: Mapped[list[int] | None] = mapped_column(JSON)
    # Tamaño del prompt (#73): estimado y el que contó el servidor (si lo devuelve), y lo que
    # se quitó para que cupiera en la ventana del modelo (ver `PromptTrim`).
    prompt_tokens_estimated: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_trim: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AuditRunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"  # terminó, pero el modelo no pudo revisar alguna pregunta
    FAILED = "failed"


class AuditRun(Base):
    """Ejecución del proceso de revisión de la auditoría (#50)."""

    __tablename__ = "audit_runs"
    # Como mucho una ejecución en curso (también entre procesos).
    __table_args__ = (
        Index(
            "uq_audit_runs_running",
            "status",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # scheduled (programada), manual (administración) o cli (línea de comandos).
    trigger: Mapped[str] = mapped_column(String(16))
    triggered_by: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=AuditRunStatus.RUNNING.value)
    # Preguntas revisadas: ids de `question_log` en (from_id, to_id].
    from_id: Mapped[int] = mapped_column(Integer, default=0)
    to_id: Mapped[int | None] = mapped_column(Integer)
    reviewed: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by_model: Mapped[int] = mapped_column(Integer, default=0)
    model_errors: Mapped[int] = mapped_column(Integer, default=0)
    alerts: Mapped[int] = mapped_column(Integer, default=0)
    anonymized: Mapped[int] = mapped_column(Integer, default=0)
    used_model: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    notification_error: Mapped[str | None] = mapped_column(Text)


class AuditAlert(Base):
    """Posible problema detectado en una pregunta o respuesta."""

    __tablename__ = "audit_alerts"
    __table_args__ = (Index("ix_audit_alerts_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    question_log_id: Mapped[int] = mapped_column(
        ForeignKey("question_log.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # malicious_user, inappropriate_response o sensitive_information.
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # low, medium, high
    source: Mapped[str] = mapped_column(String(8))  # rule o model
    reason: Mapped[str] = mapped_column(Text)
    # Fragmento que lo motiva (los datos sensibles, enmascarados).
    excerpt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new, reviewed, dismissed
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
