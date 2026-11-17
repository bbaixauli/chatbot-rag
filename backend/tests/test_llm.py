from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

from app.core.config import Settings
from app.services.llm import (
    ChatMessage,
    FakeLLMClient,
    LLMError,
    OpenAICompatibleClient,
    build_llm_client,
)


def _settings(**overrides: Any) -> Settings:
    return Settings(llm_model="modelo-x", **overrides)


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
