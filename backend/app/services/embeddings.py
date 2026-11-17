"""Generación de embeddings: remota (API compatible con OpenAI), local o simulada."""

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from app.core.config import Settings


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

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.model_name = settings.embedding_model
        self._batch_size = settings.embedding_batch_size
        api_key = settings.embedding_api_key or settings.llm_api_key
        self._client = client or AsyncOpenAI(
            base_url=settings.embedding_base_url or settings.llm_base_url,
            api_key=api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
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

    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.embedding_model
        self._batch_size = settings.embedding_batch_size
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


def build_embeddings(settings: Settings) -> Embeddings:
    """Crea el proveedor de embeddings configurado."""
    if settings.embedding_provider == "fake":
        return FakeEmbeddings()
    if settings.embedding_provider == "local":
        return LocalEmbeddings(settings)
    return OpenAICompatibleEmbeddings(settings)
