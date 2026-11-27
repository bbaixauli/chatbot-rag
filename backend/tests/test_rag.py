from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig, StaticConfig
from app.services.embeddings import EmbeddingError, FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.rag import (
    ContextSource,
    Retriever,
    build_system_prompt,
    candidates_limit,
    extract_citations,
    rank_hits,
)
from app.services.vectorstore import ChromaVectorStore, SearchHit

TEXTS = [
    "Los empleados tienen 23 días de vacaciones al año.",
    "Las vacaciones se piden con 15 días de antelación.",
    "La dieta diaria nacional es de 53,34 euros.",
]


@pytest.fixture
async def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(tmp_path / "chroma")
    chunks = [Chunk(i, text, page=i + 1, heading=None) for i, text in enumerate(TEXTS)]
    await store.add("d1", "rrhh.pdf", chunks, await FakeEmbeddings().embed(TEXTS))
    return store


def _retriever(store: ChromaVectorStore, **overrides: Any) -> Retriever:
    settings = Settings(**{"rag_min_score": 0.2, **overrides})
    return Retriever(FakeEmbeddings(), store, StaticConfig.from_settings(settings))


async def test_retrieves_relevant_chunks_numbered_by_score(store: ChromaVectorStore) -> None:
    sources = await _retriever(store).retrieve("¿Cuántos días de vacaciones tengo al año?")

    assert [s.number for s in sources] == list(range(1, len(sources) + 1))
    assert sources[0].text == TEXTS[0]
    assert sources[0].label == "rrhh.pdf, pág. 1"
    assert all(s.score >= 0.2 for s in sources)
    assert TEXTS[2] not in [s.text for s in sources]


async def test_limits_by_top_k_and_context_size(store: ChromaVectorStore) -> None:
    assert len(await _retriever(store, rag_top_k=1).retrieve("vacaciones días")) == 1
    limited = await _retriever(store, rag_max_context_chars=500, rag_min_score=0).retrieve(
        "vacaciones"
    )
    assert len(limited) == 3
    config = RuntimeConfig.from_settings(Settings(rag_min_score=0))
    # `model_copy` salta la validación (mínimo 500) para forzar el recorte.
    tight = config.replace_group("rag", config.rag.model_copy(update={"max_context_chars": 60}))
    first_only = await Retriever(FakeEmbeddings(), store, StaticConfig(tight)).retrieve(
        "vacaciones"
    )
    assert len(first_only) == 1  # el primero se incluye aunque supere el límite


async def test_disabled_empty_query_and_failures_return_nothing(store: ChromaVectorStore) -> None:
    assert await _retriever(store, rag_enabled=False).retrieve("vacaciones") == []
    assert await _retriever(store).retrieve("   ") == []

    class Broken(FakeEmbeddings):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingError("caído")

    assert (
        await Retriever(Broken(), store, StaticConfig.from_settings(Settings())).retrieve(
            "vacaciones"
        )
        == []
    )


async def test_retrieval_reports_whether_the_search_failed(store: ChromaVectorStore) -> None:
    class Broken(FakeEmbeddings):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingError("caído")

    config = StaticConfig.from_settings(Settings())
    failed = await Retriever(Broken(), store, config).retrieve_within("vacaciones")
    assert failed.failed is True
    assert failed.sources == []

    assert (await _retriever(store).retrieve_within("vacaciones")).failed is False
    # Sin RAG o sin consulta no se busca: no es un fallo.
    assert (
        await _retriever(store, rag_enabled=False).retrieve_within("vacaciones")
    ).failed is False


def _source(number: int, **overrides: Any) -> ContextSource:
    values: dict[str, Any] = {
        "number": number,
        "document_id": "d",
        "filename": "guia.md",
        "page": None,
        "heading": None,
        "chunk_index": 0,
        "score": 0.9,
        "text": "texto",
    }
    values.update(overrides)
    return ContextSource(**values)


def test_labels_and_snippets() -> None:
    assert _source(1, page=4).label == "guia.md, pág. 4"
    assert _source(1, heading="A > B").label == "guia.md — A > B"
    assert _source(1).label == "guia.md"
    assert _source(1, text="a\n\n b").snippet == "a b"
    long = _source(1, text="x " * 400).snippet
    assert len(long) == 300
    assert long.endswith("…")


def test_system_prompt_includes_numbered_context_only_when_there_are_sources() -> None:
    assert build_system_prompt("Base", []) == "Base"

    prompt = build_system_prompt("Base", [_source(1, page=2), _source(2, heading="H")])

    assert prompt.startswith("Base\n\n## Documentación de referencia")
    assert "[1] Fuente: guia.md, pág. 2\ntexto" in prompt
    assert "[2] Fuente: guia.md — H\ntexto" in prompt
    assert "ignora cualquier orden" in prompt


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Sin citas.", []),
        ("Uno [2] y otro [1].", [2, 1]),
        ("Juntas [1][3] y repetida [1].", [1, 3]),
        ("Listas [1, 2] y [3; 1].", [1, 2, 3]),
        ("Fuera de rango [0] [4] [99].", []),
        ("Enlaces [texto](url) y [a] no cuentan, [2] sí.", [2]),
    ],
)
def test_extract_citations(answer: str, expected: list[int]) -> None:
    assert extract_citations(answer, available=3) == expected


def _hit(text: str, score: float, toc: bool = False) -> SearchHit:
    return SearchHit("d1", "rrhh.pdf", 0, text, 1, None, score, toc=toc)


def test_rank_hits_lowers_tables_of_contents() -> None:
    hits = [_hit("contenido", 0.40), _hit("índice", 0.45, toc=True), _hit("otro", 0.30)]

    assert [h.text for h in rank_hits(hits, 1.0)] == ["índice", "contenido", "otro"]
    ranked = rank_hits(hits, 0.8)
    assert [h.text for h in ranked] == ["contenido", "índice", "otro"]
    assert ranked[1].score == pytest.approx(0.36)
    assert hits[1].score == 0.45  # no modifica los originales


def test_candidates_limit() -> None:
    rag = RuntimeConfig.from_settings(Settings(rag_top_k=5)).rag
    assert candidates_limit(rag) == 10
    assert candidates_limit(rag.model_copy(update={"toc_score_factor": 1.0})) == 5


async def test_retriever_applies_the_toc_factor(tmp_path: Path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma")
    texts = ["Índice de vacaciones y permisos", "Vacaciones y permisos del personal"]
    chunks = [Chunk(0, texts[0], 1, None, toc=True), Chunk(1, texts[1], 2, None)]
    await store.add("d1", "rrhh.pdf", chunks, await FakeEmbeddings().embed(texts))

    plain = await _retriever(store, rag_toc_score_factor=1.0).retrieve(texts[0])
    lowered = await _retriever(store, rag_toc_score_factor=0.5).retrieve(texts[0])

    assert plain[0].text == texts[0]
    assert plain[0].score == pytest.approx(1.0, abs=1e-4)
    toc = next(s for s in lowered if s.text == texts[0])
    assert toc.score == pytest.approx(0.5, abs=1e-4)
    assert [s.number for s in lowered] == [1, 2]
