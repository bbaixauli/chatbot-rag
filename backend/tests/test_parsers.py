from pathlib import Path

import pytest

from app.db.models import DocumentKind
from app.services.ingestion.parsers import (
    ParseError,
    Section,
    kind_for_filename,
    parse_document,
    parse_html,
    parse_markdown,
)
from tests.factories import HTML, MARKDOWN, make_pdf


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("a.pdf", DocumentKind.PDF),
        ("A.PDF", DocumentKind.PDF),
        ("notas.md", DocumentKind.MARKDOWN),
        ("notas.markdown", DocumentKind.MARKDOWN),
        ("web.html", DocumentKind.HTML),
        ("web.htm", DocumentKind.HTML),
        ("hoja.xls", None),
        ("sin_extension", None),
    ],
)
def test_kind_for_filename(filename: str, kind: DocumentKind | None) -> None:
    assert kind_for_filename(filename) == kind


def test_markdown_sections_keep_heading_path() -> None:
    sections = parse_markdown(MARKDOWN)

    assert [s.heading for s in sections] == [
        None,
        "Guía de vacaciones",
        "Guía de vacaciones > Solicitud",
        "Teletrabajo",
    ]
    assert sections[0].text == "Introducción sin encabezado."
    assert "23 días laborables" in sections[1].text
    assert sections[1].text.startswith("# Guía de vacaciones")
    assert all(s.page is None for s in sections)


def test_html_extracts_visible_text_by_heading() -> None:
    sections = parse_html(HTML)

    assert [s.heading for s in sections] == [
        "Política de gastos",
        "Dietas",
        "Dietas > Kilometraje",
    ]
    text = "\n".join(s.text for s in sections)
    assert "no indexar" not in text
    assert "Menú de navegación" not in text
    assert "Pie de página" not in text
    assert text.count("Alojamiento incluido") == 1
    # Se conservan los saltos de línea del <pre>; los espacios repetidos se colapsan.
    assert "0,26 € / km\n con vehículo propio" in sections[2].text


def test_html_without_blocks_uses_all_text() -> None:
    assert parse_html("<html><body><div>Solo un div</div></body></html>") == [
        Section(text="Solo un div", heading=None)
    ]


def test_pdf_one_section_per_page_skipping_empty(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "doc.pdf", ["Primera pagina", "", "Tercera pagina"])

    sections = parse_document(path, DocumentKind.PDF)

    assert [(s.page, s.text) for s in sections] == [(1, "Primera pagina"), (3, "Tercera pagina")]


def test_scanned_pdf_without_text_is_an_error(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "scan.pdf", ["", ""])
    with pytest.raises(ParseError, match="OCR"):
        parse_document(path, DocumentKind.PDF)


def test_corrupt_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "roto.pdf"
    path.write_bytes(b"esto no es un pdf")
    with pytest.raises(ParseError, match="No se pudo leer"):
        parse_document(path, DocumentKind.PDF)


def test_empty_html_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "vacio.html"
    path.write_text("<html><script>x()</script></html>", encoding="utf-8")
    with pytest.raises(ParseError, match="no contiene texto"):
        parse_document(path, DocumentKind.HTML)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"Año{chr(0xA0)}2026".encode(), "Año 2026"),
        ("Año 2026".encode("cp1252"), "Año 2026"),
        (f"{chr(0xFEFF)}Año 2026".encode(), "Año 2026"),
        (b"\x81\x8d\x8f", "\x81\x8d\x8f"),  # bytes no válidos en utf-8 ni cp1252
    ],
)
def test_text_files_are_decoded(tmp_path: Path, raw: bytes, expected: str) -> None:
    path = tmp_path / "doc.md"
    path.write_bytes(raw)
    assert parse_document(path, DocumentKind.MARKDOWN)[0].text == expected
