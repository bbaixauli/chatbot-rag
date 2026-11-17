"""Extracción de texto con su ubicación (página o sección) desde PDF, Markdown y HTML."""

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Tag
from markdown_it import MarkdownIt

from app.db.models import DocumentKind

EXTENSIONS: dict[str, DocumentKind] = {
    ".pdf": DocumentKind.PDF,
    ".md": DocumentKind.MARKDOWN,
    ".markdown": DocumentKind.MARKDOWN,
    ".html": DocumentKind.HTML,
    ".htm": DocumentKind.HTML,
}

# Elementos HTML que no aportan contenido (navegación, scripts, formularios…).
_HTML_NOISE = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]
_HTML_BLOCKS = ["p", "li", "pre", "blockquote", "td", "th", "dt", "dd", "figcaption"]
_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


class ParseError(Exception):
    """El fichero no se puede leer o no contiene texto."""


@dataclass(frozen=True, slots=True)
class Section:
    """Fragmento de texto con su ubicación en el documento original."""

    text: str
    page: int | None = None
    heading: str | None = None


def kind_for_filename(filename: str) -> DocumentKind | None:
    """Tipo de documento según la extensión, o `None` si no se admite."""
    return EXTENSIONS.get(Path(filename).suffix.lower())


def parse_document(path: Path, kind: DocumentKind) -> list[Section]:
    """Extrae las secciones de texto de un fichero."""
    try:
        if kind is DocumentKind.PDF:
            sections = parse_pdf(path)
        elif kind is DocumentKind.MARKDOWN:
            sections = parse_markdown(_read_text(path))
        else:
            sections = parse_html(_read_text(path))
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"No se pudo leer el fichero: {exc}") from exc
    if not sections:
        message = "El documento no contiene texto extraíble"
        if kind is DocumentKind.PDF:
            message += " (¿es un PDF escaneado sin OCR?)"
        raise ParseError(message)
    return sections


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _clean(text: str) -> str:
    text = text.replace(chr(0xA0), " ")  # espacio de no separación
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf(path: Path) -> list[Section]:
    """Una sección por página, numeradas desde 1."""
    sections = []
    with pymupdf.open(path) as pdf:  # type: ignore[no-untyped-call]
        for number, page in enumerate(pdf, start=1):
            text = _clean(page.get_text("text"))
            if text:
                sections.append(Section(text=text, page=number))
    return sections


def _heading_path(stack: list[tuple[int, str]]) -> str | None:
    return " > ".join(title for _, title in stack) or None


def _push_heading(stack: list[tuple[int, str]], level: int, title: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def parse_markdown(source: str) -> list[Section]:
    """Una sección por encabezado, con la ruta de encabezados (`A > B`)."""
    lines = source.splitlines()
    tokens = MarkdownIt("commonmark").parse(source)
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    start = 0
    heading = None

    def flush(end: int) -> None:
        text = _clean("\n".join(lines[start:end]))
        if text:
            sections.append(Section(text=text, heading=heading))

    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        flush(token.map[0])
        title = tokens[index + 1].content.strip()
        _push_heading(stack, int(token.tag[1]), title)
        heading = _heading_path(stack)
        start = token.map[0]
    flush(len(lines))
    return sections


def parse_html(source: str) -> list[Section]:
    """Texto visible agrupado por encabezados; descarta scripts, navegación, etc."""
    soup = BeautifulSoup(source, "html.parser")
    for tag in soup(_HTML_NOISE):
        tag.decompose()
    root = soup.body or soup
    title = soup.title.get_text(" ", strip=True) if soup.title else None

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    heading = title
    buffer: list[str] = []

    def flush() -> None:
        text = _clean("\n\n".join(buffer))
        if text:
            sections.append(Section(text=text, heading=heading))
        buffer.clear()

    for element in root.find_all([*_HEADINGS, *_HTML_BLOCKS]):
        if not isinstance(element, Tag):  # pragma: no cover - find_all solo devuelve Tag
            continue
        if element.name in _HEADINGS:
            flush()
            _push_heading(stack, int(element.name[1]), element.get_text(" ", strip=True))
            heading = _heading_path(stack)
            continue
        # Evita duplicar texto de bloques anidados (p. ej. <p> dentro de <li>).
        if element.find_parent(_HTML_BLOCKS):
            continue
        text = element.get_text(" " if element.name != "pre" else "", strip=element.name != "pre")
        if text.strip():
            buffer.append(text)
    flush()

    if not sections:
        # HTML sin bloques reconocibles: se usa todo el texto visible.
        text = _clean(root.get_text("\n", strip=True))
        if text:
            sections.append(Section(text=text, heading=title))
    return sections
