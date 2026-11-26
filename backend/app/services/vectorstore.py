"""Almacén vectorial de fragmentos (implementación con ChromaDB embebido)."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import chromadb
import numpy as np
import numpy.typing as npt
from chromadb.api.client import Client
from chromadb.config import Settings as ChromaSettings

from app.services.ingestion.chunker import Chunk


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Fragmento recuperado y su similitud (coseno, 1 = idéntico)."""

    document_id: str
    filename: str
    chunk_index: int
    text: str
    page: int | None
    heading: str | None
    score: float
    # El fragmento parece un índice del documento (ver `ingestion/preprocess.py`).
    toc: bool = False


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """Fragmento guardado en el índice con su vector (para diagnóstico)."""

    chunk_index: int
    text: str
    page: int | None
    heading: str | None
    vector: list[float]
    toc: bool = False
    filename: str = ""


class VectorStoreError(Exception):
    """Error del índice con un mensaje comprensible para la administración."""


class VectorStore(Protocol):
    """Operaciones sobre el índice. Permite cambiar a Qdrant, pgvector, etc."""

    async def add(
        self,
        document_id: str,
        filename: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def delete_chunks_from(self, document_id: str, first_index: int) -> None:
        """Elimina los fragmentos del documento con índice >= `first_index`."""
        ...

    async def search(
        self,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Los `limit` fragmentos más parecidos, de todo el índice o solo de `document_ids`
        (en ese caso, con búsqueda exacta: son pocos fragmentos y no se pierde ninguno)."""
        ...

    async def document_chunks(
        self, document_id: str, with_vectors: bool = True
    ) -> list[StoredChunk]:
        """Fragmentos de un documento ordenados por índice, con sus vectores (o con
        vectores vacíos si `with_vectors` es falso: p. ej. para reconstruir el texto)."""
        ...

    async def chunks_by_index(self, document_id: str, indices: Sequence[int]) -> list[SearchHit]:
        """Fragmentos concretos de un documento (sin puntuación); se omiten los que no existen."""
        ...

    async def count(self) -> int: ...

    async def reset(self) -> None:
        """Vacía el índice (antes de reindexarlo todo, p. ej. tras cambiar de modelo)."""
        ...


def cosine_scores(embedding: Sequence[float], vectors: npt.ArrayLike) -> list[float]:
    """Similitud coseno de `embedding` con cada vector (0 si alguno es nulo)."""
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.size == 0:
        return []
    query = np.asarray(embedding, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.where(norms > 0, matrix @ query / norms, 0.0)
    return [float(score) for score in scores]


def _chroma_hit(text: str, metadata: Any, score: float) -> SearchHit:
    page = metadata.get("page")
    heading = metadata.get("heading")
    return SearchHit(
        document_id=str(metadata["document_id"]),
        filename=str(metadata["filename"]),
        chunk_index=int(str(metadata["chunk_index"])),
        text=text,
        page=int(str(page)) if page is not None else None,
        heading=str(heading) if heading is not None else None,
        score=score,
        toc=bool(metadata.get("toc")),
    )


class ChromaVectorStore:
    """ChromaDB persistente en disco, con distancia coseno."""

    COLLECTION = "chunks"

    def __init__(self, path: Path | None, collection: str = COLLECTION) -> None:
        """Índice persistente en `path`, o en memoria si `path` es `None` (evaluación)."""
        settings = ChromaSettings(anonymized_telemetry=False)
        if path is None:
            self._client = chromadb.EphemeralClient(settings=settings)
        else:
            path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(path), settings=settings)
        self._collection = self._client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        self._closed = False

    async def close(self) -> None:
        """Libera el índice (Chroma comparte un sistema por ruta dentro del proceso: si no
        se cierra, otro cliente en la misma ruta lo reutiliza aunque se hayan borrado sus
        ficheros, p. ej. al restaurar una copia)."""
        if not self._closed:
            self._closed = True
            # `ClientAPI` no declara `close`, pero el cliente que devuelve Chroma la tiene.
            await asyncio.to_thread(cast(Client, self._client).close)

    async def reset(self) -> None:
        # Se recrea la colección: la nueva no tiene fijada la dimensión de los vectores.
        name = self._collection.name
        await asyncio.to_thread(self._client.delete_collection, name)
        self._collection = await asyncio.to_thread(
            self._client.get_or_create_collection,
            name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

    def drop(self) -> None:
        """Elimina la colección (índices temporales)."""
        self._client.delete_collection(self._collection.name)

    async def add(
        self,
        document_id: str,
        filename: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if not chunks:
            return
        metadatas: list[Any] = []
        for chunk in chunks:
            metadata: dict[str, str | int] = {
                "document_id": document_id,
                "filename": filename,
                "chunk_index": chunk.index,
            }
            if chunk.page is not None:
                metadata["page"] = chunk.page
            if chunk.heading:
                metadata["heading"] = chunk.heading
            # Siempre explícita: al reindexar, `upsert` podría conservar la marca anterior.
            metadata["toc"] = int(chunk.toc)
            metadatas.append(metadata)
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[f"{document_id}:{chunk.index}" for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=[list(e) for e in embeddings],  # type: ignore[arg-type]
            metadatas=metadatas,
        )

    async def delete_document(self, document_id: str) -> None:
        await asyncio.to_thread(self._collection.delete, where={"document_id": document_id})

    async def delete_chunks_from(self, document_id: str, first_index: int) -> None:
        where: Any = {
            "$and": [{"document_id": document_id}, {"chunk_index": {"$gte": first_index}}]
        }
        await asyncio.to_thread(self._collection.delete, where=where)

    async def search(
        self,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        if document_ids is not None:
            return await self._search_documents(embedding, limit, document_ids)
        total = await self.count()
        if total == 0:
            return []
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=cast(Any, [list(embedding)]),
            n_results=min(limit, total),
            include=["documents", "metadatas", "distances"],
        )
        documents = (result["documents"] or [[]])[0]
        metadatas = (result["metadatas"] or [[]])[0]
        distances = (result["distances"] or [[]])[0]
        return [
            _chroma_hit(text, metadata, 1.0 - float(distance))
            for text, metadata, distance in zip(documents, metadatas, distances, strict=True)
        ]

    async def _search_documents(
        self, embedding: Sequence[float], limit: int, document_ids: Sequence[str]
    ) -> list[SearchHit]:
        """Búsqueda exacta entre los fragmentos de unos documentos.

        El filtro de Chroma combinado con el índice HNSW puede saltarse resultados cuando
        quedan pocos fragmentos: se comparan todos.
        """
        if not document_ids or limit <= 0:
            return []
        where: Any = {"document_id": {"$in": list(document_ids)}}
        result = await asyncio.to_thread(
            self._collection.get, where=where, include=["documents", "metadatas", "embeddings"]
        )
        documents = result["documents"] or []
        metadatas = result["metadatas"] or []
        vectors = result["embeddings"]
        scores = cosine_scores(embedding, [] if vectors is None else vectors)
        hits = [
            _chroma_hit(text, metadata, score)
            for text, metadata, score in zip(documents, metadatas, scores, strict=True)
        ]
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    async def chunks_by_index(self, document_id: str, indices: Sequence[int]) -> list[SearchHit]:
        if not indices:
            return []
        result = await asyncio.to_thread(
            self._collection.get,
            ids=[f"{document_id}:{index}" for index in indices],
            include=["documents", "metadatas"],
        )
        documents = result["documents"] or []
        metadatas = result["metadatas"] or []
        hits = [
            _chroma_hit(text, metadata, 0.0)
            for text, metadata in zip(documents, metadatas, strict=True)
        ]
        return sorted(hits, key=lambda hit: hit.chunk_index)

    async def document_chunks(
        self, document_id: str, with_vectors: bool = True
    ) -> list[StoredChunk]:
        include: Any = ["documents", "metadatas", "embeddings"]
        result = await asyncio.to_thread(
            self._collection.get,
            where={"document_id": document_id},
            include=include if with_vectors else include[:2],
        )
        documents = result["documents"] or []
        metadatas = result["metadatas"] or []
        embeddings: Any = result["embeddings"] if with_vectors else [[] for _ in documents]
        vectors = [] if embeddings is None else embeddings
        chunks = []
        for text, metadata, vector in zip(documents, metadatas, vectors, strict=True):
            page = metadata.get("page")
            heading = metadata.get("heading")
            chunks.append(
                StoredChunk(
                    chunk_index=int(str(metadata["chunk_index"])),
                    text=text,
                    page=int(str(page)) if page is not None else None,
                    heading=str(heading) if heading is not None else None,
                    vector=[float(v) for v in vector],
                    toc=bool(metadata.get("toc")),
                    filename=str(metadata.get("filename", "")),
                )
            )
        return sorted(chunks, key=lambda c: c.chunk_index)

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)
