"""Apartados citados en la pregunta y apartados de los PDF (#74)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.runtime_config import IndexingConfig, StaticConfig
from app.db.models import DocumentKind
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.headings import (
    detect_headings,
    int_to_roman,
    ordinal_value,
    roman_to_int,
    split_by_headings,
)
from app.services.ingestion.parsers import Section, parse_document
from app.services.ingestion.preprocess import (
    build_chunks,
    looks_like_toc,
    preprocessing_signature,
)
from app.services.rag import ContextSource, Retriever, build_system_prompt
from app.services.sections import (
    HeadingKey,
    SectionReference,
    detect_references,
    document_headings,
    heading_key,
    locate,
)
from app.services.vectorstore import ChromaVectorStore, StoredChunk
from tests.conftest import ScriptedLLM, parse_sse, set_config
from tests.factories import MARKDOWN, make_pdf, pdf_bytes

TOC = (
    "Índice\n1 Objeto ..... 2\n2 Alcance ..... 2\n3 Accesos ..... 3\n"
    "3.1 Altas de usuarios ..... 3\n3.2 Bajas de usuarios ..... 4\n4 Auditoría ..... 5"
)
PAGES = [
    TOC,
    "1 Objeto\nEsta norma regula el acceso a los sistemas.\n2 Alcance\n"
    "Se aplica a todo el personal. Los pasos son:\n1. Pedir el acceso.\n2. Esperar la respuesta.",
    "3 Accesos\nLas cuentas son personales.\n3.1 Altas de usuarios\n"
    "Las altas se solicitan al CAU con el formulario A.\nEl coste es de\n4.2 millones de euros",
    "y se tramitan en dos días.\n3.2 Bajas de usuarios\n"
    "Las bajas se tramitan en un plazo máximo de 24 horas.",
    "La cuenta se conserva bloqueada 30 días tras la baja.\n4 Auditoría\n"
    "Los accesos se revisan cada trimestre.",
]


def _pages(texts: list[str] = PAGES) -> list[Section]:
    return [Section(text, page=number) for number, text in enumerate(texts, start=1)]


def _indexing(**overrides: Any) -> IndexingConfig:
    return IndexingConfig.from_settings(Settings(**overrides))  # type: ignore[return-value]


# --- Números romanos ---------------------------------------------------------------------


def test_roman_numerals() -> None:
    assert [roman_to_int(text) for text in ("I", "iv", "XIV", "MCMXC")] == [1, 4, 14, 1990]
    assert roman_to_int("IIII") is None  # mal formado
    assert roman_to_int("ABC") is None
    assert [int_to_roman(n) for n in (1, 4, 9, 14, 40, 1990)] == [
        "I", "IV", "IX", "XIV", "XL", "MCMXC",
    ]  # fmt: skip
    assert [ordinal_value(text) for text in ("5", "V", "B", "ñ")] == [5, 5, 2, None]


# --- Apartados de los PDF ---------------------------------------------------------------------


def test_pdf_pages_are_split_by_their_numbered_headings() -> None:
    pages = _pages()
    toc = {n for n, page in enumerate(pages) if looks_like_toc(page.text)}

    sections = split_by_headings(pages, toc)

    altas, bajas = "3 Accesos > 3.1 Altas de usuarios", "3 Accesos > 3.2 Bajas de usuarios"
    assert [(s.page, s.heading, s.text.splitlines()[0]) for s in sections] == [
        (1, None, "Índice"),
        (2, "1 Objeto", "1 Objeto"),
        (2, "2 Alcance", "2 Alcance"),
        (3, "3 Accesos", "3 Accesos"),
        (3, altas, "3.1 Altas de usuarios"),
        # El apartado sigue en la página siguiente.
        (4, altas, "y se tramitan en dos días."),
        (4, bajas, "3.2 Bajas de usuarios"),
        (5, bajas, "La cuenta se conserva bloqueada 30 días tras la baja."),
        (5, "4 Auditoría", "4 Auditoría"),
    ]
    # La lista numerada («1. Pedir…») y «4.2 millones» no son apartados.
    assert "1. Pedir el acceso." in sections[2].text
    assert "4.2 millones de euros" in sections[4].text


def test_number_and_title_on_separate_lines_and_keyword_headings() -> None:
    pages = _pages(
        [
            "CAPÍTULO I\nDisposiciones generales\nArtículo 1. Objeto\nRegula el acceso.\n"
            "Artículo 2. Ámbito\nTodo el personal, según el\n"
            "Artículo 5 de la Ley 39/2015.",
            "CAPÍTULO II\nAccesos\nArtículo 3\nLas altas.\nANEXO I\nFormularios\n1\nSolicitud\n"
            "Datos del solicitante.\n2\nAutorización\nFirma del responsable.",
        ]
    )

    headings = [h.text for h in detect_headings(pages, set())]

    assert headings == [
        "CAPÍTULO I. Disposiciones generales",
        "Artículo 1. Objeto",
        "Artículo 2. Ámbito",
        "CAPÍTULO II. Accesos",
        "Artículo 3",
        "ANEXO I. Formularios",
        # El anexo reinicia la numeración.
        "1 Solicitud",
        "2 Autorización",
    ]
    sections = split_by_headings(pages, set())
    assert sections[-1].heading == "ANEXO I. Formularios > 2 Autorización"
    assert sections[2].heading == "CAPÍTULO I. Disposiciones generales > Artículo 2. Ámbito"


def test_headings_must_follow_the_numbering() -> None:
    # Una lista al principio no es una numeración de apartados si los apartados la siguen.
    pages = _pages(
        [
            "1 Introducción\nTexto.\nRequisitos:\n"
            "1 Ser mayor de edad.\n2 Residir aquí.\n3 Estar inscrito.\n"
            "2 Objeto\nMás texto.\n3 Alcance\nTodo.\n4 Definiciones\nVarias.\n"
            "5 Procedimiento\nPasos.",
        ]
    )

    headings = [h.text for h in detect_headings(pages, set())]

    assert headings[0] == "1 Introducción"
    assert headings[-3:] == ["3 Alcance", "4 Definiciones", "5 Procedimiento"]
    # Sin títulos con mayúscula no hay apartados.
    assert detect_headings(_pages(["1 uno\n2 dos"]), set()) == []
    assert split_by_headings(_pages(["Sin apartados."]), set()) == _pages(["Sin apartados."])


def test_build_chunks_detects_headings_only_in_pdfs() -> None:
    indexing = _indexing(chunk_size_chars=1000, indexing_strip_repeated_lines=False)

    pdf = build_chunks(_pages(), indexing, DocumentKind.PDF.value)
    other = build_chunks(_pages(), indexing, DocumentKind.DOCX.value)
    disabled = build_chunks(
        _pages(),
        _indexing(chunk_size_chars=1000, indexing_pdf_headings=False),
        DocumentKind.PDF.value,
    )

    assert pdf[0].toc is True
    assert [c.heading for c in pdf][1:3] == ["1 Objeto", "2 Alcance"]
    assert all(chunk.heading is None for chunk in other + disabled)
    assert len(other) == len(disabled) == len(PAGES)


def test_preprocessing_signature_depends_on_the_kind() -> None:
    indexing = _indexing()
    assert preprocessing_signature(indexing, "pdf") == "v1;lineas=0.5;relleno;contexto;apartados"
    assert preprocessing_signature(indexing, "md") == "v1;lineas=0.5;relleno;contexto"
    assert preprocessing_signature(indexing) == "v1;lineas=0.5;relleno;contexto"
    disabled = _indexing(indexing_pdf_headings=False)
    assert preprocessing_signature(disabled, "pdf") == "v1;lineas=0.5;relleno;contexto"


def test_real_pdf_gets_its_headings(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "norma.pdf", PAGES)

    chunks = build_chunks(parse_document(path, DocumentKind.PDF), _indexing(), "pdf")

    assert "3 Accesos > 3.2 Bajas de usuarios" in {chunk.heading for chunk in chunks}


def test_source_label_shows_the_section_of_a_page() -> None:
    source = ContextSource(1, "d1", "norma.pdf", 4, "3 Accesos > 3.2 Bajas", 6, 1.0, "Texto")
    assert source.label == "norma.pdf, pág. 4 — 3.2 Bajas"
    joined = ContextSource(1, "d1", "norma.pdf", 4, "3.2 Bajas", 6, 1.0, "T", page_end=5)
    assert joined.label == "norma.pdf, págs. 4-5 — 3.2 Bajas"


# --- Referencias en la pregunta ------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿Qué dice el apartado 4.2 del NOR-013?", [("apartado", (4, 2), "")]),
        ("Resume la sección 2.1.3.", [("seccion", (2, 1, 3), "")]),
        ("capítulo V y anexo ii", [("capitulo", (5,), ""), ("anexo", (2,), "")]),
        ("Capítulo 5", [("capitulo", (5,), "")]),
        ("los apartados 3.1 y 3.2", [("apartado", (3, 1), ""), ("apartado", (3, 2), "")]),
        ("el artículo n.º 12", [("articulo", (12,), "")]),
        ("Anexo B", [("anexo", (2,), "")]),
        ("resume el punto de altas de usuarios del NOR-013", [("punto", (), "altas de usuarios")]),
        ("¿Qué es «Bajas de usuarios»?", [("apartado", (), "bajas de usuarios")]),
        # Falsos positivos.
        ("desde mi punto de vista", []),
        ("Se invirtieron 4.2 millones en 2024", []),
        ("de punto a punto", []),
        ("punto i", []),
        ("el apartado 4.2.", [("apartado", (4, 2), "")]),
    ],
)
def test_detect_references(question: str, expected: list[tuple[Any, ...]]) -> None:
    found = detect_references([question])
    assert [(r.kind, r.number, r.title) for r in found] == expected


def test_references_from_several_texts_are_not_repeated() -> None:
    found = detect_references(["¿Y el apartado 3.2?", "Apartado 3.2 de la norma de acceso"])
    assert found == [SectionReference("apartado 3.2", "apartado", number=(3, 2))]


def test_heading_key() -> None:
    assert heading_key("4.2 Altas de usuarios") == HeadingKey((4, 2), title="altas de usuarios")
    assert heading_key("CAPÍTULO V. Accesos") == HeadingKey((5,), "capitulo", "accesos")
    assert heading_key("Vacaciones") == HeadingKey(title="vacaciones")


# --- Localización en los fragmentos -------------------------------------------------------


def _chunk(index: int, text: str, heading: str | None = None, **extra: Any) -> StoredChunk:
    return StoredChunk(index, text, extra.pop("page", None), heading, [], filename="n.md", **extra)


def test_locate_by_section_metadata() -> None:
    chunks = [
        _chunk(0, "Índice 3.2 Bajas 4", toc=True, heading="Índice"),
        _chunk(1, "Intro", "3 Accesos"),
        _chunk(2, "Altas", "3 Accesos > 3.1 Altas"),
        _chunk(3, "Bajas uno", "3 Accesos > 3.2 Bajas de usuarios"),
        _chunk(4, "Bajas dos", "3 Accesos > 3.2 Bajas de usuarios"),
        _chunk(5, "Detalle", "3 Accesos > 3.2 Bajas de usuarios > 3.2.1 Plazos"),
        _chunk(6, "Auditoría", "4 Auditoría"),
    ]

    [section] = locate(detect_references(["¿Qué dice el apartado 3.2?"]), chunks)
    assert section.heading == "3.2 Bajas de usuarios"
    assert [c.chunk_index for c in section.chunks] == [3, 4, 5]

    # Por título; el capítulo entero, hasta el siguiente del mismo nivel.
    [by_title] = locate(detect_references(["resume la sección de bajas de usuarios"]), chunks)
    assert [c.chunk_index for c in by_title.chunks] == [3, 4, 5]
    [chapter] = locate(detect_references(["capítulo 3"]), chunks)
    assert [c.chunk_index for c in chapter.chunks] == [1, 2, 3, 4, 5]
    # Lo que no está en el documento no añade nada.
    assert locate(detect_references(["el apartado 9.1", "el punto de vista"]), chunks) == []
    assert document_headings([]) == []


def test_locate_in_the_text_of_pdfs_indexed_without_headings() -> None:
    # PDF indexado antes de #74: una página por fragmento, sin sección.
    chunks = [_chunk(n, text, page=n + 1) for n, text in enumerate(PAGES)]
    chunks[0] = _chunk(0, PAGES[0], page=1, toc=True)

    [section] = locate(detect_references(["¿Qué dice el apartado 3.2?"]), chunks)

    # Desde la página del encabezado (no la del índice) hasta la del siguiente apartado.
    assert [c.chunk_index for c in section.chunks] == [3, 4]
    [altas] = locate(detect_references(["apartado 3.1"]), chunks)
    # El apartado 3.2 no empieza al principio de su página: ella tiene el final del 3.1.
    assert [c.chunk_index for c in altas.chunks] == [2, 3]


# --- En la recuperación --------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(tmp_path / "chroma")
    indexing = _indexing(chunk_size_chars=1000, indexing_strip_repeated_lines=False)
    chunks = build_chunks(_pages(), indexing, "pdf")
    vectors = await FakeEmbeddings().embed([c.text for c in chunks])
    await store.add("d1", "norma.pdf", chunks, vectors)
    other_pages = [
        Section("3 Accesos\nOtro.\n3.1 Altas\nAltas.\n3.2 Bajas de otro\nBajas.", page=1)
    ]
    other = build_chunks(other_pages, indexing, "pdf")
    await store.add("d2", "otro.pdf", other, await FakeEmbeddings().embed([c.text for c in other]))
    return store


def _retriever(store: Any, **overrides: Any) -> Retriever:
    settings = Settings(**{"rag_top_k": 1, "rag_min_score": 0.0, **overrides})
    return Retriever(FakeEmbeddings(), store, StaticConfig.from_settings(settings))


async def test_retriever_adds_the_cited_section_first(store: ChromaVectorStore) -> None:
    retrieval = await _retriever(store).retrieve_within("¿Qué dice el apartado 3.2?", ["d1"])

    first, similar = retrieval.sources
    assert first.section == "apartado 3.2"
    assert first.label == "norma.pdf, págs. 4-5 — 3.2 Bajas de usuarios"
    assert first.text.startswith("3.2 Bajas de usuarios")
    assert "30 días tras la baja" in first.text
    assert "Auditoría" not in first.text
    assert (similar.number, similar.section) == (2, None)
    assert retrieval.located == [first]
    assert [r.text for r in retrieval.references] == ["apartado 3.2"]
    # El fragmento por similitud no repite ninguno del apartado.
    assert similar.chunk_index not in {7, 8}


async def test_cited_section_needs_a_limited_search(store: ChromaVectorStore) -> None:
    query = "¿Qué dice el apartado 3.2?"
    everywhere = await _retriever(store).retrieve_within(query)
    disabled = await _retriever(store, rag_locate_sections=False).retrieve_within(query, ["d1"])

    assert everywhere.references == disabled.references == []
    assert [s.section for s in everywhere.sources + disabled.sources] == [None, None]
    # Varios documentos: el apartado de cada uno.
    both = await _retriever(store, rag_top_k=1).retrieve_within(query, ["d1", "d2"])
    assert [s.document_id for s in both.located] == ["d1", "d2"]


async def test_cited_section_respects_the_budget(store: ChromaVectorStore) -> None:
    retriever = _retriever(store, rag_neighbor_chunks=2)
    query = "apartado 3.2"
    [*_, section] = [s for s in (await retriever.retrieve_within(query, ["d1"])).sources]

    tight = await retriever.retrieve_within(query, ["d1"], max_chars=200)

    # Solo cabe su primer fragmento, y nada más.
    [only] = tight.sources
    assert only.section == "apartado 3.2"
    assert (
        only.text == "3.2 Bajas de usuarios\nLas bajas se tramitan en un plazo máximo de 24 horas."
    )
    assert section.section is None
    # Sin sitio ni para eso, se busca como siempre.
    none = await retriever.retrieve_within(query, ["d1"], max_chars=50)
    assert none.located == []


async def test_original_question_is_also_checked(store: ChromaVectorStore) -> None:
    retrieval = await _retriever(store).retrieve_within(
        "plazo de las bajas en la norma", ["d1"], questions=["¿Y el apartado 3.2?"]
    )
    assert [s.section for s in retrieval.located] == ["apartado 3.2"]


async def test_located_sections_survive_a_search_failure(store: ChromaVectorStore) -> None:
    class Failing(FakeEmbeddings):
        async def embed(self, texts: Any) -> list[list[float]]:
            raise RuntimeError("caído")

    config = StaticConfig.from_settings(Settings(rag_top_k=1))
    retrieval = await Retriever(Failing(), store, config).retrieve_within("apartado 3.2", ["d1"])
    assert [s.section for s in retrieval.sources] == ["apartado 3.2"]
    nothing = await Retriever(Failing(), store, config).retrieve_within("vacaciones", ["d1"])
    assert nothing.sources == []


async def test_locate_failure_falls_back_to_the_search(store: ChromaVectorStore) -> None:
    class Broken:
        def __init__(self, inner: ChromaVectorStore) -> None:
            self.search = inner.search

        async def document_chunks(self, *args: Any, **kwargs: Any) -> list[StoredChunk]:
            raise RuntimeError("caído")

    retrieval = await _retriever(Broken(store)).retrieve_within("apartado 3.2", ["d1"])
    assert retrieval.located == []
    assert len(retrieval.sources) == 1


def test_prompt_marks_nothing_special_for_located_sections() -> None:
    source = ContextSource(1, "d1", "n.pdf", 4, "3.2 Bajas", 6, 1.0, "Texto", section="apartado")
    assert "[1] Fuente: n.pdf, pág. 4 — 3.2 Bajas\nTexto" in build_system_prompt("Base", [source])


# --- En el chat y la administración ----------------------------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    settings.rag_top_k = 1
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


async def _upload(admin: AsyncClient, app: FastAPI, name: str, data: bytes) -> str:
    response = await admin.post("/api/v1/admin/documents", files=[("files", (name, data))])
    assert response.status_code == 200, response.text
    await app.state.tasks.drain()
    document_id: str = response.json()[0]["document"]["id"]
    return document_id


async def test_chat_sends_the_cited_section(
    app: FastAPI, admin: AsyncClient, llm: ScriptedLLM, tmp_path: Path
) -> None:
    document_id = await _upload(admin, app, "norma.pdf", pdf_bytes(tmp_path, PAGES))
    await _upload(admin, app, "guia.md", MARKDOWN.encode())

    response = await admin.post(
        "/api/v1/chat",
        json={"message": "¿Qué dice el apartado 3.2?", "document_ids": [document_id]},
    )

    assert response.status_code == 200
    system = llm.calls[-1][0].content
    assert "[1] Fuente: norma.pdf, págs. 4-5 — 3.2 Bajas de usuarios\n3.2 Bajas" in system
    events = parse_sse(response.text)
    sources = next(data["sources"] for kind, data in events if kind == "sources")
    assert sources[0]["document_id"] == document_id


async def test_pdf_headings_setting_only_outdates_pdfs(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    pdf = await _upload(admin, app, "norma.pdf", pdf_bytes(tmp_path, PAGES))
    await _upload(admin, app, "guia.md", MARKDOWN.encode())
    stats = "/api/v1/admin/documents/stats"
    assert (await admin.get(stats)).json()["outdated"] == 0

    await set_config(app, "indexing", pdf_headings=False)

    assert (await admin.get(stats)).json()["outdated"] == 1
    listed = (await admin.get("/api/v1/admin/documents")).json()
    assert {d["id"] for d in listed if d["outdated"]} == {pdf}
