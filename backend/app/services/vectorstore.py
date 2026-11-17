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

    async def search(self, embedding: Sequence[float], limit: int) -> list[SearchHit]: ...

    async def count(self) -> int: ...


class ChromaVectorStore:
    """ChromaDB persistente en disco, con distancia coseno."""

    COLLECTION = "chunks"

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._collection = self._client.get_or_create_collection(
            self.COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

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

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)
