"""División de secciones en fragmentos de tamaño acotado con solapamiento."""

import re
from dataclasses import dataclass

from app.services.ingestion.parsers import Section


@dataclass(frozen=True, slots=True)
class Chunk:
    """Fragmento indexable con la ubicación de origen."""

    index: int
    text: str
    page: int | None
    heading: str | None
    # Parece un índice del documento (tabla de contenidos): la búsqueda le resta peso.
    toc: bool = False


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _units(text: str, size: int) -> list[str]:
    """Divide en párrafos y, si un párrafo es demasiado grande, en frases o trozos."""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            while len(sentence) > size:
                units.append(sentence[:size])
                sentence = sentence[size:]
            if sentence.strip():
                units.append(sentence.strip())
    return units


def _tail(text: str, overlap: int) -> str:
    """Final del fragmento anterior que se repite al inicio del siguiente."""
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    cut = text[-overlap:]
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < len(cut) - 1 else cut


def chunk_sections(sections: list[Section], size: int, overlap: int) -> list[Chunk]:
    """Agrupa el texto de cada sección en fragmentos de hasta `size` caracteres.

    Los fragmentos no cruzan secciones (así conservan su página o encabezado) y se
    solapan `overlap` caracteres para no perder contexto en los cortes.
    """
    overlap = min(overlap, size // 2)
    chunks: list[Chunk] = []
    for section in sections:
        current = ""
        for unit in _units(section.text, size):
            candidate = f"{current}\n\n{unit}" if current else unit
            if len(candidate) <= size:
                current = candidate
                continue
            chunks.append(Chunk(len(chunks), current, section.page, section.heading))
            prefix = _tail(current, overlap)
            current = (
                f"{prefix}\n\n{unit}" if prefix and len(prefix) + len(unit) + 2 <= size else unit
            )
        if current:
            chunks.append(Chunk(len(chunks), current, section.page, section.heading))
    return chunks
