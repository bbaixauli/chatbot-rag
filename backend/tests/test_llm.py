from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import httpx2 as httpx
import openai
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

from app.core.config import Settings
from app.core.runtime_config import LlmConfig, RuntimeConfig
from app.services.llm import (
    ChatMessage,
    FakeLLMClient,
    LLMError,
    OpenAICompatibleClient,
    TokenUsage,
    build_llm_client,
)


def _settings(**overrides: Any) -> LlmConfig:
    return RuntimeConfig.from_settings(Settings(llm_model="modelo-x", **overrides)).llm


def _chunk(delta: str | None, with_choice: bool = True) -> ChatCompletionChunk:
    choices = [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    return ChatCompletionChunk.model_validate(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "modelo-x",
            "choices": choices if with_choice else [],
        }
    )


class FakeCompletions:
    def __init__(self, chunks: list[ChatCompletionChunk], error: Exception | None = None):
        self.chunks = chunks
        self.error = error
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> AsyncIterator[ChatCompletionChunk]:
        self.kwargs = kwargs
        if self.error:
            raise self.error

        async def iterate() -> AsyncIterator[ChatCompletionChunk]:
            for chunk in self.chunks:
                yield chunk

        return iterate()


def _client(completions: FakeCompletions) -> AsyncOpenAI:
    return cast(AsyncOpenAI, SimpleNamespace(chat=SimpleNamespace(completions=completions)))


async def test_openai_client_streams_deltas() -> None:
    completions = FakeCompletions(
        [_chunk("Ho"), _chunk(None), _chunk(None, with_choice=False), _chunk("la")]
    )
    client = OpenAICompatibleClient(_settings(llm_temperature=0.5), _client(completions))

    deltas = [d async for d in client.stream_chat([ChatMessage("user", "hola")])]

    assert deltas == ["Ho", "la"]
    assert completions.kwargs == {
        "model": "modelo-x",
        "temperature": 0.5,
        "messages": [{"role": "user", "content": "hola"}],
        "stream": True,
    }


async def test_openai_client_wraps_sdk_errors() -> None:
    completions = FakeCompletions([], error=openai.OpenAIError("caído"))
    client = OpenAICompatibleClient(_settings(), _client(completions))

    with pytest.raises(LLMError, match="caído"):
        _ = [d async for d in client.stream_chat([ChatMessage("user", "hola")])]


def test_openai_client_builds_sdk_client_from_settings() -> None:
    client = OpenAICompatibleClient(
        _settings(llm_base_url="http://ia:8000/v1", llm_timeout_seconds=30, llm_max_retries=1)
    )
    sdk = client._client
    assert str(sdk.base_url) == "http://ia:8000/v1/"
    assert sdk.timeout == 30
    assert sdk.max_retries == 1


async def test_fake_client_echoes_last_question() -> None:
    client = FakeLLMClient(delay_seconds=0)
    messages = [ChatMessage("system", "s"), ChatMessage("user", "uno"), ChatMessage("user", "dos")]

    answer = "".join([d async for d in client.stream_chat(messages)])

    assert answer == "Respuesta simulada a: **dos**"


def test_build_llm_client_by_provider() -> None:
    assert isinstance(build_llm_client(_settings(llm_provider="fake")), FakeLLMClient)
    assert isinstance(build_llm_client(_settings()), OpenAICompatibleClient)


_REQUEST = httpx.Request("POST", "http://ia/v1/chat/completions")


def _status_error(cls: type[openai.APIStatusError], code: int, message: str) -> Exception:
    return cls(message, response=httpx.Response(code, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (openai.APITimeoutError(request=_REQUEST), "tardado demasiado"),
        (openai.APIConnectionError(request=_REQUEST), "No se pudo conectar"),
        (_status_error(openai.RateLimitError, 429, "slow down"), "saturado"),
        (_status_error(openai.AuthenticationError, 401, "bad key"), "rechazado la conexión"),
        (_status_error(openai.PermissionDeniedError, 403, "no"), "rechazado la conexión"),
        (
            _status_error(openai.BadRequestError, 400, "maximum context length is 8192 tokens"),
            "demasiado larga",
        ),
        (_status_error(openai.BadRequestError, 400, "invalid temperature"), "no ha aceptado"),
        (_status_error(openai.NotFoundError, 404, "model not found"), "no existe"),
        (_status_error(openai.InternalServerError, 500, "boom"), "no está disponible"),
        (openai.OpenAIError("otro"), "no está disponible"),
    ],
)
async def test_sdk_errors_get_user_friendly_messages(error: Exception, expected: str) -> None:
    client = OpenAICompatibleClient(_settings(), _client(FakeCompletions([], error=error)))

    with pytest.raises(LLMError) as info:
        _ = [d async for d in client.stream_chat([ChatMessage("user", "hola")])]

    assert expected in info.value.user_message
    assert type(error).__name__ in str(info.value)


# --- Tokens que cuenta el servidor (#73) ---------------------------------------------------


def _usage_chunk(prompt: int, completion: int) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "modelo-x",
            "choices": [],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
    )


async def test_the_server_token_count_is_requested_and_read() -> None:
    completions = FakeCompletions([_chunk("Hola"), _usage_chunk(1200, 3)])
    client = OpenAICompatibleClient(_settings(), _client(completions))
    usage = TokenUsage()

    deltas = [d async for d in client.stream_chat([ChatMessage("user", "hola")], usage=usage)]

    assert deltas == ["Hola"]
    assert completions.kwargs["stream_options"] == {"include_usage": True}
    assert (usage.prompt_tokens, usage.completion_tokens) == (1200, 3)


class StrictCompletions(FakeCompletions):
    """Servidor que no admite `stream_options`."""

    def __init__(self) -> None:
        super().__init__([_chunk("Hola")])
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(kwargs)
        if "stream_options" in kwargs:
            raise _status_error(openai.BadRequestError, 400, "Unknown field: stream_options")
        return await super().create(**kwargs)


async def test_servers_without_stream_options_answer_without_the_count() -> None:
    completions = StrictCompletions()
    client = OpenAICompatibleClient(_settings(), _client(completions))
    usage = TokenUsage()

    for _ in range(2):
        answer = [d async for d in client.stream_chat([ChatMessage("user", "h")], usage=usage)]
        assert answer == ["Hola"]

    assert usage.prompt_tokens is None
    # Solo se intenta una vez: después se pide siempre sin el recuento.
    assert ["stream_options" in call for call in completions.calls] == [True, False, False]


async def test_other_bad_requests_are_not_retried() -> None:
    error = _status_error(openai.BadRequestError, 400, "invalid temperature")
    completions = FakeCompletions([], error=error)
    client = OpenAICompatibleClient(_settings(), _client(completions))

    with pytest.raises(LLMError, match="invalid temperature"):
        _ = [d async for d in client.stream_chat([ChatMessage("user", "h")], usage=TokenUsage())]
