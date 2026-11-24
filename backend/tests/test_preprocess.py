from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.runtime_config import IndexingConfig
from app.db.models import DocumentKind
from app.services.ingestion.parsers import Section, parse_document
from app.services.ingestion.preprocess import (
    build_chunks,
    clean_toc_leaders,
    document_title,
    embedding_text,
    embedding_texts,
    preprocessing_signature,
    strip_repeated_lines,
)
from tests.factories import make_pdf

# Sello de firma electrónica que se repite en cada página, con la fecha y la página
# cambiando de una a otra.
STAMP = """Código Seguro de Verificación
IVVH2PBIZ4PCXTUMSVWFE4RBL4
Fecha {day:02d}/04/2026 14:12:51
Firmado por: Unidad de Normativa
Página {page} de 5"""

TOC_PAGE = """ÍNDICE
1. INTRODUCCIÓN ........................................ 1
1.1. Objetivo .......................................... 1
1.2. Alcance ........................................... 2
2. CONTROL DE ACCESO ................................... 3
2.1. Altas de usuarios ................................. 3
2.2. Bajas de usuarios ................................. 4"""


def _indexing(**overrides: Any) -> IndexingConfig:
    return IndexingConfig.from_settings(Settings(**overrides))  # type: ignore[return-value]


def _signed_pages(contents: list[str]) -> list[Section]:
    return [
        Section(text=f"{STAMP.format(day=page, page=page)}\n\n{content}", page=page)
        for page, content in enumerate(contents, start=1)
    ]


def test_strip_repeated_lines_removes_stamps_and_page_numbers() -> None:
    contents = [
        "Las contraseñas caducan cada 90 días.",
        "Las altas se solicitan al CAU.\nVer anexo I.",
        "Las bajas se tramitan en 24 horas.\nVer anexo I.",
        "El acceso remoto requiere doble factor.",
        "Las cuentas inactivas se bloquean a los 60 días.",
    ]

    result = strip_repeated_lines(_signed_pages(contents), min_ratio=0.5)

    assert [s.text for s in result] == contents  # «Ver anexo I» solo está en 2 de 5 páginas
    assert [s.page for s in result] == [1, 2, 3, 4, 5]


def test_strip_repeated_lines_respects_the_ratio_and_drops_empty_pages() -> None:
    pages = _signed_pages(["Uno", "Dos", "Tres", "Cuatro"])
    pages.append(Section(text="Final", page=5))
    pages.append(Section(text=STAMP.format(day=6, page=6), page=6))

    # El sello está en 5 de 6 páginas: si se exige que esté en todas, no se quita…
    assert [s.text for s in strip_repeated_lines(pages, min_ratio=1)] == [s.text for s in pages]
    # …y con la mitad sí; la última página se queda sin texto y se descarta.
    assert [s.text for s in strip_repeated_lines(pages, min_ratio=0.5)] == [
        "Uno",
        "Dos",
        "Tres",
        "Cuatro",
        "Final",
    ]


def test_strip_repeated_lines_leaves_other_documents_alone() -> None:
    short = _signed_pages(["Uno", "Dos"])
    assert strip_repeated_lines(short, 0.5) == short  # menos de 3 páginas
    sections = [Section(text=STAMP, heading=f"Apartado {i}") for i in range(4)]
    assert strip_repeated_lines(sections, 0.5) == sections  # sin páginas (Markdown, HTML)
    identical = [Section(text="Igual en todas", page=i) for i in range(1, 5)]
    assert strip_repeated_lines(identical, 0.5) == identical  # no quedaría nada


def test_clean_toc_leaders() -> None:
    assert clean_toc_leaders("1.1. Objetivo ............ 1") == "1.1. Objetivo 1"
    assert clean_toc_leaders("Alcance . . . . . . 2\nOtro ······ 3") == "Alcance 2\nOtro 3"
    assert clean_toc_leaders("Anexo……… 4") == "Anexo 4"
    # Puntos suspensivos y numeraciones no son relleno.
    assert clean_toc_leaders("Y así sucesivamente...") == "Y así sucesivamente..."
    assert clean_toc_leaders("Apartado 1.2.3.4 vigente") == "Apartado 1.2.3.4 vigente"


def test_document_title() -> None:
    assert document_title("NOR-013 CONTROL_DE_ACCESO  v3.2.pdf") == "NOR-013 CONTROL DE ACCESO v3.2"
    assert document_title("guia.md") == "guia"


def test_build_chunks_marks_tables_of_contents_and_cleans_them() -> None:
    pages = _signed_pages([TOC_PAGE, "Contenido de la página dos.", "Tres", "Cuatro", "Cinco"])

    chunks = build_chunks(pages, _indexing())

    assert chunks[0].toc is True
    assert "...." not in chunks[0].text
    assert "1.1. Objetivo 1" in chunks[0].text
    assert "Código Seguro" not in chunks[0].text
    assert [c.toc for c in chunks[1:]] == [False] * 4
    assert chunks[1].text == "Contenido de la página dos."


def test_build_chunks_without_preprocessing_keeps_the_text() -> None:
    pages = _signed_pages([TOC_PAGE, "Dos", "Tres"])
    indexing = _indexing(indexing_strip_repeated_lines=False, indexing_clean_toc_leaders=False)

    chunks = build_chunks(pages, indexing)

    assert chunks[0].text == pages[0].text
    assert chunks[0].toc is True  # la marca no depende de la limpieza


def test_embedding_text_adds_the_document_and_section() -> None:
    indexing = _indexing()
    assert embedding_text("Texto", "NOR-013 Acceso.pdf", "Altas > Usuarios", indexing) == (
        "Documento: NOR-013 Acceso\nSección: Altas > Usuarios\n\nTexto"
    )
    assert embedding_text("Texto", "guia.pdf", None, indexing) == "Documento: guia\n\nTexto"
    disabled = _indexing(indexing_context_prefix=False)
    assert embedding_text("Texto", "guia.pdf", "Algo", disabled) == "Texto"
    chunks = build_chunks([Section(text="Hola", heading="Saludo")], indexing)
    assert embedding_texts(chunks, "a.md", indexing) == ["Documento: a\nSección: Saludo\n\nHola"]


@pytest.mark.parametrize(
    ("overrides", "signature"),
    [
        ({}, "v1;lineas=0.5;relleno;contexto"),
        ({"indexing_repeated_line_min_ratio": 0.75}, "v1;lineas=0.75;relleno;contexto"),
        (
            {
                "indexing_strip_repeated_lines": False,
                "indexing_clean_toc_leaders": False,
                "indexing_context_prefix": False,
            },
            "v1",
        ),
    ],
)
def test_preprocessing_signature(overrides: dict[str, Any], signature: str) -> None:
    assert preprocessing_signature(_indexing(**overrides)) == signature


def test_signed_pdf_is_indexed_without_the_stamp(tmp_path: Path) -> None:
    contents = [TOC_PAGE, "Las altas se solicitan al CAU.", "Las bajas, en 24 horas."]
    pages = [f"{STAMP.format(day=n, page=n)}\n{text}" for n, text in enumerate(contents, start=1)]
    sections = parse_document(make_pdf(tmp_path / "firmado.pdf", pages), DocumentKind.PDF)

    chunks = build_chunks(sections, _indexing())

    assert not any("Verificación" in c.text or "Página" in c.text for c in chunks)
    assert [c.toc for c in chunks] == [True, False, False]
    assert [c.text for c in chunks[1:]] == contents[1:]
    assert [c.page for c in chunks] == [1, 2, 3]
