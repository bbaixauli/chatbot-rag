"""Fragmentos vecinos y unión de fragmentos en pasajes (#58, fase 4)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.runtime_config import StaticConfig
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk, chunk_sections
from app.services.ingestion.parsers import Section
from app.services.passages import contiguous_runs, join_chunks, overlap_length
from app.services.rag import ContextSource, Retriever
from app.services.vectorstore import ChromaVectorStore, SearchHit

PARAGRAPHS = [
    f"Párrafo {n}: las cuentas de usuario del servicio {n} se revisan cada trimestre "
    f"por la unidad responsable."
    for n in range(1, 13)
]


def test_join_chunks_removes_the_overlap() -> None:
    chunks = chunk_sections([Section(text="\n\n".join(PARAGRAPHS))], 300, 80)
    assert len(chunks) > 3
    # Cada fragmento repite el final del anterior…
    assert overlap_length(chunks[0].text, chunks[1].text) >= 20
    # …y al unirlos queda el texto original, sin repeticiones.
    assert join_chunks([c.text for c in chunks]) == "\n\n".join(PARAGRAPHS)


def test_join_chunks_without_overlap_and_edge_cases() -> None:
    assert join_chunks([]) == ""
    assert join_chunks(["Solo uno."]) == "Solo uno."
    # Una coincidencia corta (un punto, una palabra) no es solapamiento.
    assert join_chunks(["Fin de la página 3.", "3. Siguiente apartado."]) == (
        "Fin de la página 3.\n\n3. Siguiente apartado."
    )
    # Si el siguiente está contenido en el anterior, no se añade nada.
    assert join_chunks(["Texto largo con un final compartido.", "con un final compartido."]) == (
        "Texto largo con un final compartido."
    )


def test_contiguous_runs() -> None:
    assert contiguous_runs([8, 1, 3, 2, 7, 2]) == [[1, 2, 3], [7, 8]]
    assert contiguous_runs([]) == []


def test_source_label_and_snippet_of_a_passage() -> None:
    source = ContextSource(
        number=1,
        document_id="d1",
        filename="norma.pdf",
        page=4,
        heading=None,
        chunk_index=5,
        score=0.5,
        text="Antes. Encontrado. Después.",
        page_end=5,
        match_text="Encontrado.",
    )
    assert source.label == "norma.pdf, págs. 4-5"
    assert source.snippet == "Encontrado."
    single = ContextSource(1, "d1", "norma.pdf", 4, None, 5, 0.5, "Texto", page_end=4)
    assert single.label == "norma.pdf, pág. 4"


# --- En la recuperación --------------------------------------------------------------------

TEXTS = [
    "Introducción a la normativa de seguridad de la información.",
    "Ámbito de aplicación: todo el personal y los sistemas de DTIC.",
    "Las contraseñas caducan cada 90 días y deben tener 12 caracteres.",
    "Tras 5 intentos fallidos la cuenta se bloquea durante 30 minutos.",
    "Las altas de usuarios se solicitan al centro de atención (CAU).",
    "Las bajas se tramitan en un plazo máximo de 24 horas.",
]


@pytest.fixture
async def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(tmp_path / "chroma")
    chunks = [Chunk(i, text, page=i + 1, heading=None) for i, text in enumerate(TEXTS)]
    await store.add("d1", "norma.pdf", chunks, await FakeEmbeddings().embed(TEXTS))
    return store


def _retriever(store: Any, **overrides: Any) -> Retriever:
    settings = Settings(**{"rag_min_score": 0.5, "rag_top_k": 1, **overrides})
    return Retriever(FakeEmbeddings(), store, StaticConfig.from_settings(settings))


async def test_neighbors_are_added_and_joined(store: ChromaVectorStore) -> None:
    [plain] = await _retriever(store).retrieve(TEXTS[3])
    assert plain.text == TEXTS[3]
    assert plain.label == "norma.pdf, pág. 4"

    [passage] = await _retriever(store, rag_neighbor_chunks=1).retrieve(TEXTS[3])

    assert passage.text == "\n\n".join(TEXTS[2:5])
    assert passage.label == "norma.pdf, págs. 3-5"
    assert (passage.chunk_index, passage.snippet) == (3, TEXTS[3])
    assert passage.score == pytest.approx(1.0, abs=1e-4)

    [wider] = await _retriever(store, rag_neighbor_chunks=3).retrieve(TEXTS[0])
    assert wider.text == "\n\n".join(TEXTS[0:4])  # no hay fragmentos antes del primero


async def test_neighbors_respect_the_context_limit(store: ChromaVectorStore) -> None:
    retriever = _retriever(store, rag_neighbor_chunks=2)
    rag = retriever._config.current.rag
    # Solo cabe un vecino más: el más cercano.
    limit = len(TEXTS[3]) + len(TEXTS[2]) + 5
    tight = rag.model_copy(update={"max_context_chars": limit})
    config = retriever._config.current.replace_group("rag", tight)

    [passage] = await Retriever(FakeEmbeddings(), store, StaticConfig(config)).retrieve(TEXTS[3])

    assert passage.text == "\n\n".join(TEXTS[2:4])


async def test_close_results_share_a_passage(store: ChromaVectorStore) -> None:
    sources = await _retriever(
        store, rag_neighbor_chunks=1, rag_top_k=2, rag_min_score=0.0
    ).retrieve(f"{TEXTS[2]} {TEXTS[4]}")

    assert len(sources) == 1
    assert sources[0].text == "\n\n".join(TEXTS[1:6])
    assert sources[0].number == 1


async def test_neighbor_failures_fall_back_to_the_results(store: ChromaVectorStore) -> None:
    class Broken:
        def __init__(self, inner: ChromaVectorStore) -> None:
            self._inner = inner

        async def search(self, *args: Any) -> list[SearchHit]:
            return await self._inner.search(*args)

        async def chunks_by_index(self, document_id: str, indices: Sequence[int]) -> Any:
            raise RuntimeError("caído")

    [source] = await _retriever(Broken(store), rag_neighbor_chunks=1).retrieve(TEXTS[3])

    assert source.text == TEXTS[3]


async def test_chroma_chunks_by_index(store: ChromaVectorStore) -> None:
    hits = await store.chunks_by_index("d1", [4, 1, 99])

    assert [(h.chunk_index, h.text, h.page) for h in hits] == [(1, TEXTS[1], 2), (4, TEXTS[4], 5)]
    assert await store.chunks_by_index("d1", []) == []
    assert await store.chunks_by_index("otro", [0]) == []
