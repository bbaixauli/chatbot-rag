"""Esquemas de entrada y salida de la API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    document_id: str | None
    filename: str
    page: int | None
    heading: str | None
    snippet: str
    cited: bool


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sources: list[SourceOut] = []


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1)
