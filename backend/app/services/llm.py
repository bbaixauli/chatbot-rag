"""Clientes del modelo de lenguaje."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Mensaje en el formato que espera el modelo."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMError(Exception):
    """Error al comunicarse con el modelo de lenguaje."""


class LLMClient(Protocol):
    """Cliente de un modelo de chat con respuesta en streaming."""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Devuelve los fragmentos de texto de la respuesta según se generan."""
        ...


class OpenAICompatibleClient:
    """Cliente para cualquier servidor compatible con la API de OpenAI."""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        payload = cast(
            list[ChatCompletionMessageParam],
            [{"role": m.role, "content": m.content} for m in messages],
        )
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                messages=payload,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and (delta := chunk.choices[0].delta.content):
                    yield delta
        except openai.OpenAIError as exc:
            raise LLMError(f"Error del modelo de lenguaje: {exc}") from exc


class FakeLLMClient:
    """Modelo simulado y determinista.

    Repite la última pregunta y, si el prompt de sistema incluye documentación,
    cita el primer fragmento ([1]).
    """

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self._delay = delay_seconds

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        question = next((m.content for m in reversed(messages) if m.role == "user"), "")
        system = next((m.content for m in messages if m.role == "system"), "")
        if system.startswith("Reformula la pregunta"):
            # Reformulación simulada: preguntas anteriores del usuario + la de seguimiento.
            lines = question.splitlines()
            asked = [
                line.removeprefix("Usuario: ") for line in lines if line.startswith("Usuario: ")
            ]
            follow_up = next(
                (
                    line.removeprefix("Pregunta de seguimiento: ")
                    for line in lines
                    if line.startswith("Pregunta de seguimiento: ")
                ),
                "",
            )
            yield " ".join([*asked, follow_up])
            return
        answer = f"Respuesta simulada a: **{question}**"
        if "\n[1] Fuente:" in system:
            answer += " Según la documentación [1]."
        for index, word in enumerate(answer.split(" ")):
            await asyncio.sleep(self._delay)
            yield word if index == 0 else f" {word}"


def build_llm_client(settings: Settings) -> LLMClient:
    """Crea el cliente configurado."""
    if settings.llm_provider == "fake":
        return FakeLLMClient()
    return OpenAICompatibleClient(settings)
