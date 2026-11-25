from pathlib import Path

import pytest

from app.db.models import DocumentKind
from app.services.ingestion.titles import MAX_TITLE_LENGTH, clean_title, extract_title
from tests.factories import make_pdf


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "ab",
        "1234",
        "Untitled",
        "Sin título",
        "Documento1",
        "Documento 12",
        "Presentación1",
        "Libro1",
        "Microsoft Word - borrador3.docx",
        "Microsoft PowerPoint - Presentación de la empresa",
        "informe final.docx",
        "manual.pdf",
        r"C:\Users\ana\Escritorio\informe",
    ],
)
def test_discards_titles_that_are_not_real_titles(raw: str | None) -> None:
    assert clean_title(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Normativa de teletrabajo", "Normativa de teletrabajo"),
        ("  Guía   de\nvacaciones ", "Guía de vacaciones"),
        ("NOR-013 Control de acceso v1.2", "NOR-013 Control de acceso v1.2"),
        ("Documento de seguridad", "Documento de seguridad"),
        ("Política 1/2024", "Política 1/2024"),
    ],
)
def test_keeps_real_titles(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected


def test_long_titles_are_shortened() -> None:
    title = clean_title("Título " * 100)
    assert title is not None
    assert len(title) == MAX_TITLE_LENGTH
    assert title.endswith("…")


def test_pdf_title_from_metadata(tmp_path: Path) -> None:
    with_title = make_pdf(tmp_path / "a.pdf", ["Texto"], title="Manual del empleado")
    office = make_pdf(tmp_path / "b.pdf", ["Texto"], title="Microsoft Word - manual.docx")
    without = make_pdf(tmp_path / "c.pdf", ["Texto"])

    assert extract_title(with_title, DocumentKind.PDF) == "Manual del empleado"
    assert extract_title(office, DocumentKind.PDF) is None
    assert extract_title(without, DocumentKind.PDF) is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "<html><head><title> Política de gastos </title></head><body></body></html>",
            "Política de gastos",
        ),
        (
            '<html><head><meta property="og:title" content="Gastos de viaje"></head></html>',
            "Gastos de viaje",
        ),
        ("<html><body><h1>Guía <em>rápida</em></h1></body></html>", "Guía rápida"),
        # El <title> no sirve («Documento1»): se usa el encabezado.
        (
            "<html><head><title>Documento1</title></head><body><h1>Dietas</h1></body></html>",
            "Dietas",
        ),
        ("<html><body><p>Sin título</p></body></html>", None),
    ],
)
def test_html_title(tmp_path: Path, source: str, expected: str | None) -> None:
    path = tmp_path / "a.html"
    path.write_text(source, encoding="utf-8")
    assert extract_title(path, DocumentKind.HTML) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("---\ntitle: Guía del empleado\nautor: RR. HH.\n---\n# Otra cosa\n", "Guía del empleado"),
        ('---\ntitle: "Entre comillas"\n---\nTexto', "Entre comillas"),
        ("---\nautor: RR. HH.\n---\n# Encabezado\n", "Encabezado"),
        ("Introducción.\n\n# Guía de vacaciones\n\n## Solicitud\n", "Guía de vacaciones"),
        ("## Solo de segundo nivel\n\nTexto", None),
        ("Texto sin encabezados", None),
    ],
)
def test_markdown_title(tmp_path: Path, source: str, expected: str | None) -> None:
    path = tmp_path / "a.md"
    path.write_text(source, encoding="utf-8")
    assert extract_title(path, DocumentKind.MARKDOWN) == expected


def test_unreadable_files_have_no_title(tmp_path: Path) -> None:
    path = tmp_path / "roto.pdf"
    path.write_bytes(b"no es un pdf")
    assert extract_title(path, DocumentKind.PDF) is None
