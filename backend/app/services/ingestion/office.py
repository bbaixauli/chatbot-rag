"""Extracción de texto de documentos de Office y OpenDocument (#76).

Formatos modernos: Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`) y sus equivalentes
de LibreOffice (`.odt`, `.odp`, `.ods`). Los binarios antiguos (`.doc`, `.ppt`, `.xls`) no
se admiten.

Ubicación citable de cada formato:

- Textos (Word, ODT): el apartado (ruta de encabezados, como en Markdown).
- Presentaciones: el número de diapositiva (como «página») y su título como apartado.
- Hojas de cálculo: la hoja como apartado. Cada fila se convierte en «Columna: valor; …»
  con las cabeceras de la primera fila, para que se entienda sin el resto de la tabla.
"""

import re
import zipfile
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import docx
import pptx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from odf import dc, teletype
from odf import style as odf_style
from odf import table as odf_table
from odf import text as odf_text
from odf.element import Element
from odf.namespaces import DRAWNS, PRESENTATIONNS, STYLENS, TABLENS, TEXTNS
from odf.opendocument import load as load_odf
from openpyxl import load_workbook
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.shapes.base import BaseShape

from app.db.models import DocumentKind
from app.services.ingestion.parsers import (
    ParseError,
    Section,
    clean_text,
    heading_path,
    push_heading,
)

# Contenedor OLE: documentos de Office cifrados con contraseña o binarios antiguos.
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# Encabezados de Word: «Heading 1», «Título 1», «Encabezado 1».
_HEADING_STYLE = re.compile(r"^(?:heading|t[ií]tulo|encabezado)\s*(\d)$", re.IGNORECASE)
# Celdas repetidas de OpenDocument que se expanden como mucho (las vacías no se expanden:
# las hojas declaran a veces un millón de filas o columnas vacías repetidas).
_MAX_REPEAT = 100


def check_container(path: Path) -> None:
    """Error comprensible si el fichero está cifrado o no es un formato moderno."""
    with path.open("rb") as file:
        magic = file.read(len(_OLE_MAGIC))
    if magic == _OLE_MAGIC:
        raise ParseError(
            "El fichero está protegido con contraseña o tiene un formato antiguo de Office "
            "(.doc, .xls, .ppt), que no se admite: guárdalo sin contraseña como .docx, .xlsx "
            "o .pptx"
        )
    if not zipfile.is_zipfile(path):
        raise ParseError("El fichero está dañado o no es un documento de Office válido")
    with zipfile.ZipFile(path) as archive:
        if "META-INF/manifest.xml" in archive.namelist():
            manifest = archive.read("META-INF/manifest.xml")
            if b"encryption-data" in manifest:
                raise ParseError("El fichero está protegido con contraseña")


def _rows_text(rows: Iterable[list[str]]) -> str:
    """Filas de una hoja: la primera no vacía es la cabecera; cada una de las demás se
    convierte en «Cabecera: valor; …» (sin las celdas vacías)."""
    header: list[str] | None = None
    lines: list[str] = []
    for row in rows:
        if not any(row):
            continue
        if header is None:
            header = row
            continue
        pairs = [
            f"{header[i] if i < len(header) and header[i] else f'Columna {i + 1}'}: {value}"
            for i, value in enumerate(row)
            if value
        ]
        lines.append("; ".join(pairs))
    if header is not None and not lines:
        # Solo una fila: se indexa tal cual.
        lines.append(" | ".join(cell for cell in header if cell))
    return "\n\n".join(lines)


def _table_text(rows: Iterable[list[str]]) -> str:
    """Tabla de un texto (Word, ODT): una línea por fila, celdas separadas por «|»."""
    lines = []
    for row in rows:
        # Las celdas combinadas se repiten: se dejan una vez.
        cells = [cell for i, cell in enumerate(row) if cell and (i == 0 or cell != row[i - 1])]
        if cells:
            lines.append(" | ".join(cells))
    return "\n\n".join(lines)


def _cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time() else value.isoformat(" ")
    if isinstance(value, date | time):
        return value.isoformat()
    return " ".join(str(value).split())


class _Outline:
    """Acumula el texto de un documento por apartados (ruta de encabezados)."""

    def __init__(self) -> None:
        self.sections: list[Section] = []
        self._stack: list[tuple[int, str]] = []
        self._heading: str | None = None
        self._buffer: list[str] = []

    def heading(self, level: int, title: str) -> None:
        self.flush()
        push_heading(self._stack, level, title)
        self._heading = heading_path(self._stack)

    def add(self, text: str) -> None:
        if text.strip():
            self._buffer.append(text)

    def flush(self) -> None:
        text = clean_text("\n\n".join(self._buffer))
        if text:
            self.sections.append(Section(text=text, heading=self._heading))
        self._buffer.clear()


# --- Word -------------------------------------------------------------------------------


def _docx_level(paragraph: DocxParagraph) -> int | None:
    style = paragraph.style
    match = _HEADING_STYLE.match((style.name or "") if style is not None else "")
    return int(match.group(1)) if match else None


def parse_docx(path: Path) -> list[Section]:
    document = docx.Document(str(path))
    outline = _Outline()
    for block in document.iter_inner_content():
        if isinstance(block, DocxTable):
            outline.add(
                _table_text([cell.text.strip() for cell in row.cells] for row in block.rows)
            )
            continue
        text = block.text.strip()
        level = _docx_level(block)
        if level is not None and text:
            outline.heading(level, text)
        else:
            outline.add(text)
    outline.flush()
    return outline.sections


# --- PowerPoint -------------------------------------------------------------------------


def _shape_texts(shape: BaseShape) -> Iterator[str]:
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:  # type: ignore[attr-defined]
            yield from _shape_texts(child)
        return
    if getattr(shape, "has_text_frame", False):
        yield shape.text_frame.text  # type: ignore[attr-defined]
    if getattr(shape, "has_table", False):
        rows = shape.table.rows  # type: ignore[attr-defined]
        yield _table_text([cell.text.strip() for cell in row.cells] for row in rows)


def parse_pptx(path: Path) -> list[Section]:
    """Una sección por diapositiva visible (con sus notas), numeradas desde 1."""
    presentation = pptx.Presentation(str(path))
    sections = []
    for number, slide in enumerate(presentation.slides, start=1):
        if slide.element.get("show") == "0":  # diapositiva oculta
            continue
        title_shape = slide.shapes.title
        title = " ".join(title_shape.text_frame.text.split()) if title_shape is not None else ""
        texts = [text for shape in slide.shapes for text in _shape_texts(shape)]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            texts.append(slide.notes_slide.notes_text_frame.text)
        text = clean_text("\n\n".join(t for t in texts if t.strip()))
        if text:
            sections.append(Section(text=text, page=number, heading=title or None))
    return sections


# --- Excel ------------------------------------------------------------------------------


def parse_xlsx(path: Path) -> list[Section]:
    """Una sección por hoja visible, con una línea «Cabecera: valor» por fila."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sections = []
        for sheet in workbook.worksheets:
            if sheet.sheet_state != "visible":
                continue
            rows = ([_cell_value(v) for v in row] for row in sheet.iter_rows(values_only=True))
            text = clean_text(_rows_text(rows))
            if text:
                sections.append(Section(text=text, heading=sheet.title))
        return sections
    finally:
        workbook.close()


# --- OpenDocument -----------------------------------------------------------------------


def _attribute(element: Element, namespace: str, name: str) -> str | None:
    value = element.attributes.get((namespace, name))
    return str(value) if value is not None else None


def _repeat(element: Element, name: str) -> int:
    return int(_attribute(element, TABLENS, name) or 1)


def _odf_rows(table: Element) -> Iterator[list[str]]:
    for row in table.getElementsByType(odf_table.TableRow):
        cells: list[str] = []
        for cell in row.childNodes:
            if cell.qname not in ((TABLENS, "table-cell"), (TABLENS, "covered-table-cell")):
                continue
            text = " ".join(teletype.extractText(cell).split())
            cells.extend(
                [text] * (min(_repeat(cell, "number-columns-repeated"), _MAX_REPEAT) if text else 1)
            )
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            yield from [cells] * min(_repeat(row, "number-rows-repeated"), _MAX_REPEAT)


def _odt_walk(node: Element, outline: _Outline) -> None:
    for child in node.childNodes:
        qname = getattr(child, "qname", None)
        if qname == (TEXTNS, "h"):
            level = int(_attribute(child, TEXTNS, "outline-level") or 1)
            title = " ".join(teletype.extractText(child).split())
            if title:
                outline.heading(level, title)
        elif qname == (TEXTNS, "p"):
            outline.add(teletype.extractText(child).strip())
        elif qname == (TABLENS, "table"):
            outline.add(_table_text(_odf_rows(child)))
        elif getattr(child, "childNodes", None):
            # Listas, secciones, marcos…
            _odt_walk(child, outline)


def parse_odt(path: Path) -> list[Section]:
    document = load_odf(str(path))
    outline = _Outline()
    _odt_walk(document.text, outline)
    outline.flush()
    return outline.sections


def parse_odp(path: Path) -> list[Section]:
    """Una sección por diapositiva (con sus notas), numeradas desde 1."""
    document = load_odf(str(path))
    sections = []
    pages = [p for p in document.presentation.childNodes if p.qname == (DRAWNS, "page")]
    for number, page in enumerate(pages, start=1):
        title = ""
        for frame in page.childNodes:
            if _attribute(frame, PRESENTATIONNS, "class") == "title":
                title = " ".join(teletype.extractText(frame).split())
                break
        texts = [teletype.extractText(p).strip() for p in page.getElementsByType(odf_text.P)]
        text = clean_text("\n\n".join(t for t in texts if t))
        if text:
            sections.append(Section(text=text, page=number, heading=title or None))
    return sections


def parse_ods(path: Path) -> list[Section]:
    """Una sección por hoja visible, con una línea «Cabecera: valor» por fila."""
    document = load_odf(str(path))
    # Las hojas ocultas lo están por su estilo (table:display="false").
    hidden = {
        _attribute(style, STYLENS, "name")
        for style in document.automaticstyles.getElementsByType(odf_style.Style)
        for properties in style.getElementsByType(odf_style.TableProperties)
        if _attribute(properties, TABLENS, "display") == "false"
    }
    sections = []
    for sheet in document.spreadsheet.getElementsByType(odf_table.Table):
        if _attribute(sheet, TABLENS, "style-name") in hidden:
            continue
        text = clean_text(_rows_text(_odf_rows(sheet)))
        if text:
            sections.append(Section(text=text, heading=_attribute(sheet, TABLENS, "name")))
    return sections


_PARSERS = {
    DocumentKind.DOCX: parse_docx,
    DocumentKind.PPTX: parse_pptx,
    DocumentKind.XLSX: parse_xlsx,
    DocumentKind.ODT: parse_odt,
    DocumentKind.ODP: parse_odp,
    DocumentKind.ODS: parse_ods,
}


def parse_office(path: Path, kind: DocumentKind) -> list[Section]:
    """Secciones de un documento de Office u OpenDocument."""
    check_container(path)
    return _PARSERS[kind](path)


# --- Títulos ----------------------------------------------------------------------------


def office_title(path: Path, kind: DocumentKind) -> str | None:
    """Título de las propiedades del documento (sin validar)."""
    check_container(path)
    if kind is DocumentKind.DOCX:
        return str(docx.Document(str(path)).core_properties.title or "") or None
    if kind is DocumentKind.PPTX:
        return str(pptx.Presentation(str(path)).core_properties.title or "") or None
    if kind is DocumentKind.XLSX:
        workbook = load_workbook(path, read_only=True)
        try:
            title: str | None = workbook.properties.title
            return title
        finally:
            workbook.close()
    document = load_odf(str(path))
    titles = document.meta.getElementsByType(dc.Title)
    return teletype.extractText(titles[0]) if titles else None
