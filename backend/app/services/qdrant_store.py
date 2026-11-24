"""Almacén vectorial en Qdrant (servidor externo), para volúmenes grandes de documentos.

Requiere el extra `escalado` (`uv sync --extra escalado`).
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.services.ingestion.chunker import Chunk
from app.services.vectorstore import SearchHit, StoredChunk, VectorStoreError

logger = logging.getLogger(__name__)

# Espera a que esté lista una colección que acaba de crear otro worker.
COLLECTION_READY_ATTEMPTS = 20
COLLECTION_READY_DELAY_SECONDS = 0.25

# Espacio de nombres para derivar ids estables (Qdrant solo admite UUID o enteros).
_POINT_NAMESPACE = uuid.UUID("6f1c9a52-3c5e-4d51-9a3e-0c2b7d1f4e88")


def point_id(document_id: str, chunk_index: int) -> str:
    """Id del punto de un fragmento: reindexar sustituye el mismo punto (upsert)."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{document_id}:{chunk_index}"))


class QdrantVectorStore:
    """Colección de Qdrant con distancia coseno.

    La colección se crea al indexar el primer fragmento, con la dimensión de sus vectores.
    Varios workers pueden intentar crearla a la vez: si otro se adelanta, se usa la suya.
    Si cambia el modelo de embeddings (y por tanto la dimensión), «Reindexar todo» vacía
    el índice antes de volver a indexar (ver `DocumentService.reindex_all`).
    """

    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    @classmethod
    def from_url(cls, url: str, api_key: str | None, collection: str) -> "QdrantVectorStore":
        # Sin comprobar la versión al crear el cliente: evita una llamada de red (y un aviso)
        # al arrancar si Qdrant aún no está disponible.
        client = AsyncQdrantClient(url=url, api_key=api_key, timeout=30, check_compatibility=False)
        return cls(client, collection)

    async def close(self) -> None:
        await self._client.close()

    async def _collection_dimension(self) -> int | None:
        """Dimensión de la colección, o `None` si no existe."""
        if not await self._client.collection_exists(self._collection):
            return None
        info = await self._client.get_collection(self._collection)
        vectors = info.config.params.vectors
        return vectors.size if isinstance(vectors, models.VectorParams) else None

    async def _wait_for_collection(self) -> int | None:
        """Dimensión de una colección recién creada por otro proceso.

        Justo después de crearla, Qdrant puede responder con errores mientras la inicializa.
        """
        for attempt in range(COLLECTION_READY_ATTEMPTS):
            try:
                return await self._collection_dimension()
            except Exception:
                if attempt == COLLECTION_READY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(COLLECTION_READY_DELAY_SECONDS)
        return None  # pragma: no cover

    async def _ensure_collection(self, dimension: int) -> None:
        # Se comprueba en cada documento (no se guarda en memoria): otro proceso puede
        # haber vaciado el índice («Reindexar todo», restaurar una copia) y el worker
        # debe volver a crear la colección.
        current = await self._collection_dimension()
        if current is None:
            try:
                await self._client.create_collection(
                    self._collection,
                    vectors_config=models.VectorParams(
                        size=dimension, distance=models.Distance.COSINE
                    ),
                )
                # Índices para filtrar por documento al borrar o reindexar.
                for field, schema in (
                    ("document_id", models.PayloadSchemaType.KEYWORD),
                    ("chunk_index", models.PayloadSchemaType.INTEGER),
                ):
                    await self._client.create_payload_index(self._collection, field, schema)
            except Exception:
                # Otro worker la ha creado a la vez: vale si coincide la dimensión.
                current = await self._wait_for_collection()
                if current is None:
                    raise
                logger.info("La colección %s la ha creado otro proceso", self._collection)
            else:
                current = dimension
        if current != dimension:
            raise VectorStoreError(
                f"El índice tiene vectores de {current} dimensiones y el modelo de embeddings "
                f"genera {dimension}. Pulsa «Reindexar todo» en la administración."
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
        await self._ensure_collection(len(embeddings[0]))
        points = [
            models.PointStruct(
                id=point_id(document_id, chunk.index),
                vector=list(vector),
                payload={
                    "document_id": document_id,
                    "filename": filename,
                    "chunk_index": chunk.index,
                    "text": chunk.text,
                    "page": chunk.page,
                    "heading": chunk.heading or None,
                    "toc": chunk.toc,
                },
            )
            for chunk, vector in zip(chunks, embeddings, strict=True)
        ]
        await self._client.upsert(self._collection, points=points, wait=True)

    async def _delete(self, conditions: list[models.Condition]) -> None:
        if not await self._client.collection_exists(self._collection):
            return
        await self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(filter=models.Filter(must=conditions)),
            wait=True,
        )

    async def delete_document(self, document_id: str) -> None:
        await self._delete(
            [models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
        )

    async def delete_chunks_from(self, document_id: str, first_index: int) -> None:
        await self._delete(
            [
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                ),
                models.FieldCondition(key="chunk_index", range=models.Range(gte=first_index)),
            ]
        )

    async def search(
        self,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        if document_ids is not None and (not document_ids or limit <= 0):
            return []
        if not await self._client.collection_exists(self._collection):
            return []
        # Limitada a unos documentos: con filtro y búsqueda exacta (pocos fragmentos).
        query_filter = (
            None
            if document_ids is None
            else models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id", match=models.MatchAny(any=list(document_ids))
                    )
                ]
            )
        )
        result = await self._client.query_points(
            self._collection,
            query=list(embedding),
            limit=limit,
            with_payload=True,
            query_filter=query_filter,
            search_params=None if document_ids is None else models.SearchParams(exact=True),
        )
        return [_hit(point.payload or {}, point.score) for point in result.points]

    async def chunks_by_index(self, document_id: str, indices: Sequence[int]) -> list[SearchHit]:
        if not indices or not await self._client.collection_exists(self._collection):
            return []
        points = await self._client.retrieve(
            self._collection,
            ids=[point_id(document_id, index) for index in indices],
            with_payload=True,
        )
        hits = [_hit(point.payload or {}, 0.0) for point in points]
        return sorted(hits, key=lambda hit: hit.chunk_index)

    async def document_chunks(self, document_id: str) -> list[StoredChunk]:
        if not await self._client.collection_exists(self._collection):
            return []
        condition = models.FieldCondition(
            key="document_id", match=models.MatchValue(value=document_id)
        )
        chunks: list[StoredChunk] = []
        offset: Any = None
        while True:
            points, offset = await self._client.scroll(
                self._collection,
                scroll_filter=models.Filter(must=[condition]),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for point in points:
                payload = point.payload or {}
                page = payload.get("page")
                heading = payload.get("heading")
                vector = point.vector
                if not isinstance(vector, list):
                    raise VectorStoreError("La colección no usa un único vector por fragmento")
                chunks.append(
                    StoredChunk(
                        chunk_index=int(payload["chunk_index"]),
                        text=str(payload["text"]),
                        page=int(page) if page is not None else None,
                        heading=str(heading) if heading else None,
                        vector=[float(v) for v in vector],  # type: ignore[arg-type]
                        toc=bool(payload.get("toc")),
                    )
                )
            if offset is None:
                break
        return sorted(chunks, key=lambda c: c.chunk_index)

    async def count(self) -> int:
        if not await self._client.collection_exists(self._collection):
            return 0
        return (await self._client.count(self._collection, exact=True)).count

    async def reset(self) -> None:
        """Elimina la colección (p. ej. al restaurar una copia de seguridad)."""
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)


def _hit(payload: dict[str, Any], score: float) -> SearchHit:
    page = payload.get("page")
    heading = payload.get("heading")
    return SearchHit(
        document_id=str(payload["document_id"]),
        filename=str(payload["filename"]),
        chunk_index=int(payload["chunk_index"]),
        text=str(payload["text"]),
        page=int(page) if page is not None else None,
        heading=str(heading) if heading else None,
        score=float(score),
        toc=bool(payload.get("toc")),
    )
