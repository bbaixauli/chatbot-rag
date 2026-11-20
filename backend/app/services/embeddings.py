"""Generación de embeddings: remota (API compatible con OpenAI), local o simulada."""

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from app.core.runtime_config import ConfigSource, EmbeddingConfig, RuntimeConfig
from app.services.llm import api_key_value


class EmbeddingError(Exception):
    """Error al calcular embeddings."""


class Embeddings(Protocol):
    """Convierte textos en vectores."""

    @property
    def model_name(self) -> str:
        """Identificador del modelo (se guarda con cada documento indexado)."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Devuelve un vector por texto, en el mismo orden."""
        ...


class OpenAICompatibleEmbeddings:
    """Embeddings vía `/v1/embeddings` (LiteLLM, vLLM, Ollama, OpenAI…)."""

    def __init__(self, config: RuntimeConfig, client: AsyncOpenAI | None = None) -> None:
        embeddings, llm = config.embeddings, config.llm
        self.model_name = embeddings.model
        self._batch_size = embeddings.batch_size
        api_key = (
            embeddings.api_key.get_secret_value() if embeddings.api_key else api_key_value(llm)
        )
        self._client = client or AsyncOpenAI(
            base_url=embeddings.base_url or llm.base_url,
            api_key=api_key,
            timeout=llm.timeout_seconds,
            max_retries=llm.max_retries,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), self._batch_size):
                batch = list(texts[start : start + self._batch_size])
                response = await self._client.embeddings.create(model=self.model_name, input=batch)
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors.extend(item.embedding for item in ordered)
        except openai.OpenAIError as exc:
            raise EmbeddingError(f"Error del servicio de embeddings: {exc}") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                "El servicio de embeddings devolvió un número de vectores distinto"
            )
        return vectors


class LocalEmbeddings:
    """Embeddings con sentence-transformers en el propio servidor (CPU o GPU)."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.model_name = config.model
        self._batch_size = config.batch_size
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "Instala las dependencias opcionales: uv sync --extra local-embeddings"
            ) from exc
        return SentenceTransformer(self.model_name)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
        vectors = await asyncio.to_thread(
            self._model.encode,
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
        )
        return [list(map(float, vector)) for vector in vectors]


class FakeEmbeddings:
    """Embeddings deterministas por hashing de palabras (sin red ni modelo).

    Textos que comparten palabras obtienen vectores parecidos, suficiente para probar
    la búsqueda de extremo a extremo.
    """

    model_name = "fake-hashing"

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
            vector[int.from_bytes(digest) % self._dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


def build_embeddings(config: RuntimeConfig) -> Embeddings:
    """Crea el proveedor de embeddings configurado."""
    if config.embeddings.provider == "fake":
        return FakeEmbeddings()
    if config.embeddings.provider == "local":
        return LocalEmbeddings(config.embeddings)
    return OpenAICompatibleEmbeddings(config)


def _connection(config: RuntimeConfig) -> tuple[object, ...]:
    """Lo que determina el cliente de embeddings (cambios en otros campos no lo recrean:
    recargar un modelo local es costoso)."""
    if config.embeddings.provider != "openai":
        return (config.embeddings,)
    llm = config.llm
    return (config.embeddings, llm.base_url, llm.api_key, llm.timeout_seconds, llm.max_retries)


class ConfigurableEmbeddings:
    """Embeddings que siguen la configuración vigente: si cambia, se crea otro cliente."""

    def __init__(self, source: ConfigSource) -> None:
        self._source = source
        self._key: tuple[object, ...] | None = None
        self._embeddings: Embeddings | None = None

    def _current(self) -> Embeddings:
        config = self._source.current
        key = _connection(config)
        if self._embeddings is None or key != self._key:
            self._embeddings = build_embeddings(config)
            self._key = key
        return self._embeddings

    @property
    def model_name(self) -> str:
        return self._current().model_name

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._current().embed(texts)
