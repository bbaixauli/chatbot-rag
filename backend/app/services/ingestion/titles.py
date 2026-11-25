"""Título de un documento a partir de sus metadatos (#75).

El título es lo que se muestra en las referencias y se pasa al indexar. Se toma de los
metadatos del fichero (PDF, Office, OpenDocument), del título de la página (HTML) o del
primer encabezado o el front matter (Markdown). Los metadatos suelen traer basura
(«Microsoft Word - borrador3.docx», «Documento1», el nombre del fichero…): esos títulos se
descartan y se usa el nombre del fichero.
"""

import re
import unicodedata
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup

from app.db.models import DocumentKind
from app.services.ingestion.parsers import read_text

MAX_TITLE_LENGTH = 255
MIN_TITLE_LENGTH = 3

# Títulos genéricos que ponen los programas por defecto (normalizados: minúsculas y sin
# acentos), con o sin número detrás.
_GENERIC = re.compile(
    r"^(?:untitled|sin titulo|titulo|title|document|documento|doc|nuevo documento|"
    r"new document|presentacion|presentation|libro|book|diapositiva|slide|hoja|sheet|"
    r"plantilla|template)(?: ?\d+)?$"
)
# «Microsoft Word - informe.docx» y similares (los añade la impresora de PDF de Office).
_OFFICE_PREFIX = re.compile(r"^microsoft (?:office )?(?:word|powerpoint|excel) ?- ")
# Parece un nombre de fichero o una ruta.
_FILE_NAME = re.compile(
    r"\.(?:pdf|docx?|dotx?|pptx?|xlsx?|od[tps]|rtf|txt|html?|md|markdown|indd|tex)$"
)
# Front matter YAML de Markdown («---» … «---» al principio).
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONT_MATTER_TITLE = re.compile(r"^title[ \t]*:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_MARKDOWN_H1 = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def clean_title(raw: str | None) -> str | None:
    """El título sin espacios de más, o `None` si no sirve como título."""
    if not raw:
        return None
    title = " ".join(raw.replace("\x00", " ").split())
    if len(title) < MIN_TITLE_LENGTH or not any(ch.isalpha() for ch in title):
        return None
    plain = _plain(title)
    if _GENERIC.match(plain) or _OFFICE_PREFIX.match(plain) or _FILE_NAME.search(plain):
        return None
    if "\\" in title:  # ruta de Windows
        return None
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return title


def _pdf_title(path: Path) -> str | None:
    with pymupdf.open(path) as pdf:  # type: ignore[no-untyped-call]
        metadata = pdf.metadata or {}
    return metadata.get("title")


def html_title(source: str) -> str | None:
    soup = BeautifulSoup(source, "html.parser")
    candidates: list[str | None] = []
    if soup.title is not None:
        candidates.append(soup.title.get_text())
    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        content = og.get("content")
        candidates.append(content if isinstance(content, str) else None)
    h1 = soup.find("h1")
    if h1 is not None:
        candidates.append(h1.get_text(" "))
    return next((title for title in map(clean_title, candidates) if title), None)


def _markdown_title(source: str) -> str | None:
    front = _FRONT_MATTER.match(source)
    if front:
        match = _FRONT_MATTER_TITLE.search(front.group(1))
        if match:
            title = clean_title(match.group(1).strip().strip("\"'"))
            if title:
                return title
        source = source[front.end() :]
    heading = _MARKDOWN_H1.search(source)
    return heading.group(1) if heading else None


def extract_title(path: Path, kind: DocumentKind) -> str | None:
    """Título de los metadatos del fichero, o `None` si no tiene uno válido (o no se puede
    leer: el error, si lo hay, se verá al indexarlo)."""
    try:
        if kind is DocumentKind.PDF:
            raw = _pdf_title(path)
        elif kind in (DocumentKind.MARKDOWN, DocumentKind.WEB):
            raw = _markdown_title(read_text(path))
        elif kind is DocumentKind.HTML:
            raw = html_title(read_text(path))
        else:
            from app.services.ingestion.office import office_title

            raw = office_title(path, kind)
    except Exception:
        return None
    return clean_title(raw)
