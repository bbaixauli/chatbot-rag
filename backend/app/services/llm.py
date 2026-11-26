"""Clientes del modelo de lenguaje."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.runtime_config import ConfigSource, LlmConfig


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Mensaje en el formato que espera el modelo."""

    role: Literal["system", "user", "assistant"]
    content: str


logger = logging.getLogger(__name__)

NO_API_KEY = "not-needed"
DEFAULT_USER_MESSAGE = "El modelo no está disponible. Inténtalo de nuevo."


class LLMError(Exception):
    """Error al comunicarse con el modelo de lenguaje.

    `user_message` es apto para mostrarlo en el chat; el detalle técnico queda en el log.
    """

    def __init__(self, message: str, user_message: str = DEFAULT_USER_MESSAGE) -> None:
        super().__init__(message)
        self.user_message = user_message


_CONTEXT_LENGTH_HINTS = ("context length", "context_length", "maximum context", "too many tokens")


def user_message_for(exc: openai.OpenAIError) -> str:
    """Mensaje comprensible para el usuario según el tipo de error del servidor de IA."""
    if isinstance(exc, openai.APITimeoutError):
        return "El modelo ha tardado demasiado en responder. Inténtalo de nuevo."
    if isinstance(exc, openai.APIConnectionError):
        return "No se pudo conectar con el modelo. Inténtalo de nuevo en unos minutos."
    if isinstance(exc, openai.RateLimitError):
        return "El modelo está saturado. Espera unos segundos e inténtalo de nuevo."
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return "El servidor de IA ha rechazado la conexión. Avisa al administrador."
    if isinstance(exc, openai.BadRequestError):
        if any(hint in str(exc).lower() for hint in _CONTEXT_LENGTH_HINTS):
            return "La conversación es demasiado larga para el modelo. Empieza una nueva."
        return "El modelo no ha aceptado la petición. Avisa al administrador."
    if isinstance(exc, openai.NotFoundError):
        return "El modelo configurado no existe en el servidor de IA. Avisa al administrador."
    return DEFAULT_USER_MESSAGE


@dataclass(slots=True)
class TokenUsage:
    """Tokens que ha contado el servidor (si los devuelve; ver `stream_options`)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMClient(Protocol):
    """Cliente de un modelo de chat con respuesta en streaming."""

    def stream_chat(
        self, messages: Sequence[ChatMessage], *, usage: TokenUsage | None = None
    ) -> AsyncIterator[str]:
        """Devuelve los fragmentos de texto de la respuesta según se generan. Si se pasa
        `usage`, al terminar tiene los tokens que contó el servidor (si los devuelve)."""
        ...


class OpenAICompatibleClient:
    """Cliente para cualquier servidor compatible con la API de OpenAI."""

    def __init__(self, config: LlmConfig, client: AsyncOpenAI | None = None) -> None:
        self._model = config.model
        self._temperature = config.temperature
        # Se pide el recuento de tokens (#73) salvo que el servidor no admita la opción.
        self._usage_supported = True
        self._client = client or AsyncOpenAI(
            base_url=config.base_url,
            api_key=api_key_value(config),
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    async def _create(self, payload: list[ChatCompletionMessageParam], usage: bool) -> Any:
        if not usage:
            return await self._client.chat.completions.create(
                model=self._model, temperature=self._temperature, messages=payload, stream=True
            )
        return await self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=payload,
            stream=True,
            stream_options={"include_usage": True},
        )

    async def stream_chat(
        self, messages: Sequence[ChatMessage], *, usage: TokenUsage | None = None
    ) -> AsyncIterator[str]:
        payload = cast(
            list[ChatCompletionMessageParam],
            [{"role": m.role, "content": m.content} for m in messages],
        )
        try:
            ask_usage = usage is not None and self._usage_supported
            try:
                stream = await self._create(payload, ask_usage)
            except openai.BadRequestError as exc:
                if not ask_usage or "stream_options" not in str(exc):
                    raise
                # Servidor que no admite `stream_options`: se pide sin el recuento.
                logger.info("El servidor del modelo no admite stream_options; sin recuento")
                self._usage_supported = False
                stream = await self._create(payload, False)
            async for chunk in stream:
                if chunk.choices and (delta := chunk.choices[0].delta.content):
                    yield delta
                if usage is not None and chunk.usage is not None:
                    usage.prompt_tokens = chunk.usage.prompt_tokens
                    usage.completion_tokens = chunk.usage.completion_tokens
        except openai.OpenAIError as exc:
            raise LLMError(
                f"Error del modelo de lenguaje ({type(exc).__name__}): {exc}",
                user_message_for(exc),
            ) from exc


class FakeLLMClient:
    """Modelo simulado y determinista.

    Repite la última pregunta y, si el prompt de sistema incluye documentación,
    cita el primer fragmento ([1]).
    """

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self._delay = delay_seconds

    async def stream_chat(
        self, messages: Sequence[ChatMessage], *, usage: TokenUsage | None = None
    ) -> AsyncIterator[str]:
        question = next((m.content for m in reversed(messages) if m.role == "user"), "")
        system = next((m.content for m in messages if m.role == "system"), "")
        if system.startswith("Eres el auditor de seguridad"):
            # Revisión simulada (auditoría): marca como maliciosa una pregunta que contenga
            # «simula ataque» (para pruebas y demos sin modelo).
            malicious = "simula ataque" in question.lower()
            yield json.dumps(
                {
                    "malicious_user": malicious,
                    "inappropriate_response": False,
                    "sensitive_information": False,
                    "severity": "high" if malicious else "low",
                    "reason": "Pregunta de prueba marcada como ataque" if malicious else "",
                }
            )
            return
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


def api_key_value(config: LlmConfig) -> str:
    """Clave de la API; los servidores sin autenticación aceptan cualquier valor."""
    return config.api_key.get_secret_value() if config.api_key else NO_API_KEY


def build_llm_client(config: LlmConfig) -> LLMClient:
    """Crea el cliente configurado."""
    if config.provider == "fake":
        return FakeLLMClient()
    return OpenAICompatibleClient(config)


class ConfigurableLLM:
    """Cliente que sigue la configuración vigente: si cambia, crea uno nuevo.

    Las respuestas en curso terminan con el cliente con el que empezaron.
    """

    def __init__(self, source: ConfigSource) -> None:
        self._source = source
        self._config: LlmConfig | None = None
        self._client: LLMClient | None = None

    def _current(self) -> LLMClient:
        config = self._source.current.llm
        if self._client is None or config != self._config:
            self._client = build_llm_client(config)
            self._config = config
        return self._client

    def stream_chat(
        self, messages: Sequence[ChatMessage], *, usage: TokenUsage | None = None
    ) -> AsyncIterator[str]:
        return self._current().stream_chat(messages, usage=usage)
