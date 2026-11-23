"""Modelos ORM de la aplicación."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
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


class Conversation(Base):
    """Conversación del chat."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Nulo/`anonymous` mientras no haya autenticación; preparado para LDAP.
    user_id: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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


class Document(Base):
    """Fichero subido para el RAG."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(255))
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
    uploaded_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # Auditoría (#50): quién, desde dónde y con qué se respondió. La IP, el navegador y el
    # nombre se anonimizan pasado el plazo configurado.
    client_ip: Mapped[str | None] = mapped_column(String(45), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user_display_name: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version_id: Mapped[int | None] = mapped_column(Integer)
    # Consulta con la que se buscó en los documentos (la pregunta reformulada).
    search_query: Mapped[str | None] = mapped_column(Text)
    # [{"document_id", "filename", "page", "cited"}] de las fuentes mostradas.
    cited_documents: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error_detail: Mapped[str | None] = mapped_column(Text)
