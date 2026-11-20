import math
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.runtime_config import EmbeddingConfig, RuntimeConfig
from app.services.embeddings import (
    EmbeddingError,
    FakeEmbeddings,
    LocalEmbeddings,
    OpenAICompatibleEmbeddings,
    build_embeddings,
)


def _config(**overrides: Any) -> RuntimeConfig:
    return RuntimeConfig.from_settings(Settings(**overrides))


def _embedding_config(**overrides: Any) -> EmbeddingConfig:
    return _config(**overrides).embeddings


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_fake_embeddings_are_normalized_and_semantic() -> None:
    fake = FakeEmbeddings()
    vacaciones, otra, nada = await fake.embed(
        ["días de vacaciones al año", "¿cuántos días de vacaciones tengo?", "kilometraje"]
    )

    assert math.isclose(math.sqrt(sum(v * v for v in vacaciones)), 1.0)
    assert _cosine(vacaciones, otra) > _cosine(vacaciones, nada)
    assert await fake.embed([""]) == [[0.0] * 256]


class FakeEmbeddingsAPI:
    def __init__(self, error: Exception | None = None, drop: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error
        self.drop = drop

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        items = [
            SimpleNamespace(index=i, embedding=[float(len(text))])
            for i, text in enumerate(kwargs["input"])
        ]
        if self.drop:
            items = items[:-1]
        return SimpleNamespace(data=list(reversed(items)))


def _remote(api: FakeEmbeddingsAPI, **overrides: Any) -> OpenAICompatibleEmbeddings:
    settings = Settings(embedding_model="emb", embedding_batch_size=2, **overrides)
    client = cast(AsyncOpenAI, SimpleNamespace(embeddings=api))
    return OpenAICompatibleEmbeddings(RuntimeConfig.from_settings(settings), client)


async def test_remote_embeddings_batch_and_keep_order() -> None:
    api = FakeEmbeddingsAPI()

    vectors = await _remote(api).embed(["a", "bb", "ccc"])

    assert vectors == [[1.0], [2.0], [3.0]]
    assert [call["input"] for call in api.calls] == [["a", "bb"], ["ccc"]]
    assert api.calls[0]["model"] == "emb"


async def test_remote_embeddings_wrap_errors_and_mismatches() -> None:
    with pytest.raises(EmbeddingError, match="caído"):
        await _remote(FakeEmbeddingsAPI(error=openai.OpenAIError("caído"))).embed(["a"])
    with pytest.raises(EmbeddingError, match="número de vectores"):
        await _remote(FakeEmbeddingsAPI(drop=True)).embed(["a"])


def test_remote_embeddings_default_to_llm_endpoint() -> None:
    settings = Settings(llm_base_url="http://litellm:4000/v1")
    assert (
        str(OpenAICompatibleEmbeddings(RuntimeConfig.from_settings(settings))._client.base_url)
        == "http://litellm:4000/v1/"
    )

    settings = Settings(embedding_base_url="http://emb:9000/v1", embedding_api_key="k")
    client = OpenAICompatibleEmbeddings(RuntimeConfig.from_settings(settings))._client
    assert str(client.base_url) == "http://emb:9000/v1/"
    assert client.api_key == "k"


async def test_local_embeddings_require_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(EmbeddingError, match="local-embeddings"):
        await LocalEmbeddings(_embedding_config()).embed(["hola"])


async def test_local_embeddings_load_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []

    class FakeModel:
        def __init__(self, name: str) -> None:
            loaded.append(name)

        def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            assert kwargs["normalize_embeddings"] is True
            return [[1, 0] for _ in texts]

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    local = LocalEmbeddings(_embedding_config(embedding_model="BAAI/bge-m3"))

    assert await local.embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    await local.embed(["c"])
    assert loaded == ["BAAI/bge-m3"]


def test_build_embeddings_by_provider() -> None:
    assert isinstance(build_embeddings(_config(embedding_provider="fake")), FakeEmbeddings)
    assert isinstance(build_embeddings(_config(embedding_provider="local")), LocalEmbeddings)
    assert isinstance(build_embeddings(_config()), OpenAICompatibleEmbeddings)
