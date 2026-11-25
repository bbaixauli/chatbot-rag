"""Esquemas de entrada y salida de la API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.db.models import DocumentKind
from app.services.scope import MAX_SCOPE_DOCUMENTS

# Documentos a los que se limita la búsqueda (#58), como mucho `MAX_SCOPE_DOCUMENTS`; una
# lista vacía quita la limitación.
ScopeIds = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=36)]],
    Field(max_length=MAX_SCOPE_DOCUMENTS),
]


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    # Documentos a los que se limita la búsqueda (vacía: todos).
    document_ids: Annotated[list[str], BeforeValidator(lambda value: value or [])] = []


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    """Cambia el título o los documentos a los que se limita la búsqueda."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    document_ids: ScopeIds | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    document_id: str | None
    filename: str
    # Título actual del documento (#75); si se borró, el nombre del fichero.
    title: str = ""
    # Dirección web del documento, si se añadió por ella (#78): la referencia enlaza a ella.
    url: str | None = None
    page: int | None
    heading: str | None
    snippet: str
    cited: bool


class ScopeDocumentOut(BaseModel):
    id: str
    filename: str
    # Título actual del documento (#75); si se borró, el nombre del fichero.
    title: str = ""


class ScopeOut(BaseModel):
    """Documentos a los que se limitó la búsqueda de una respuesta (#58)."""

    documents: list[ScopeDocumentOut]
    # `selector`: elegidos en el chat; `detected`: nombrados en la pregunta.
    source: Literal["selector", "detected"]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sources: list[SourceOut] = []
    scope: ScopeOut | None = None


class ChatConfigOut(BaseModel):
    """Mensaje inicial y preguntas sugeridas al empezar una conversación."""

    model_config = ConfigDict(from_attributes=True)

    welcome_message: str | None
    suggested_questions: list[str]


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1)
    # Sin indicar: se mantiene la limitación guardada en la conversación.
    document_ids: ScopeIds | None = None


class PublicDocumentOut(BaseModel):
    """Documento indexado, para elegir dónde buscar en el chat."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    title: str
    kind: DocumentKind
    category: str | None = None
