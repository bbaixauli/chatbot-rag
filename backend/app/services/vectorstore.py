"""Almacén vectorial de fragmentos (implementación con ChromaDB embebido)."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import chromadb
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


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """Fragmento guardado en el índice con su vector (para diagnóstico)."""

    chunk_index: int
    text: str
    page: int | None
    heading: str | None
    vector: list[float]


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

    async def search(self, embedding: Sequence[float], limit: int) -> list[SearchHit]: ...

    async def document_chunks(self, document_id: str) -> list[StoredChunk]:
        """Fragmentos de un documento con sus vectores, ordenados por índice."""
        ...

    async def count(self) -> int: ...

    async def reset(self) -> None:
        """Vacía el índice (antes de reindexarlo todo, p. ej. tras cambiar de modelo)."""
        ...


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

    async def search(self, embedding: Sequence[float], limit: int) -> list[SearchHit]:
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
        hits = []
        for text, metadata, distance in zip(documents, metadatas, distances, strict=True):
            page = metadata.get("page")
            heading = metadata.get("heading")
            hits.append(
                SearchHit(
                    document_id=str(metadata["document_id"]),
                    filename=str(metadata["filename"]),
                    chunk_index=int(str(metadata["chunk_index"])),
                    text=text,
                    page=int(str(page)) if page is not None else None,
                    heading=str(heading) if heading is not None else None,
                    score=1.0 - float(distance),
                )
            )
        return hits

    async def document_chunks(self, document_id: str) -> list[StoredChunk]:
        result = await asyncio.to_thread(
            self._collection.get,
            where={"document_id": document_id},
            include=["documents", "metadatas", "embeddings"],
        )
        documents = result["documents"] or []
        metadatas = result["metadatas"] or []
        embeddings = result["embeddings"]
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
                )
            )
        return sorted(chunks, key=lambda c: c.chunk_index)

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)
