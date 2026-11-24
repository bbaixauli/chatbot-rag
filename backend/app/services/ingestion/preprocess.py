"""Preparación del texto antes de indexarlo (#62).

Lo que se extrae de los documentos no siempre es buen material para buscar:

- Muchos PDF repiten en cada página el mismo encabezado o pie (sellos de firma
  electrónica, cabeceras corporativas, «Página 3 de 27»). Ese texto ocupa parte de cada
  fragmento, aplana sus puntuaciones y hace que todos los documentos con el mismo sello se
  parezcan. Se eliminan las líneas que se repiten en la mayoría de las páginas.
- Los índices de los documentos llevan puntos de relleno («Objetivo ........ 5») que no
  aportan nada. Se reducen a un espacio.
- Un fragmento no dice de qué documento es: una pregunta que nombra el documento («el
  NOR-013») no se parece a sus fragmentos. Al calcular el vector se antepone el título del
  documento y la sección (el texto guardado y mostrado no cambia).
- Los fragmentos de índice se marcan para que la búsqueda les reste peso (ver
  `rank_hits` en `services/rag.py`).
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from app.core.runtime_config import IndexingConfig
from app.services.ingestion.chunker import Chunk, chunk_sections
from app.services.ingestion.parsers import Section

# Sube si cambia el preprocesado: los documentos indexados antes quedan desactualizados.
PREPROCESSING_VERSION = 1

# Por debajo de este número de páginas no se buscan encabezados repetidos.
MIN_PAGES_FOR_REPEATED_LINES = 3

# Líneas de un índice. Al extraer el texto de un PDF, el número de página puede quedar en la
# misma línea que el título o en una línea aparte (y los puntos de relleno, en cualquiera).
_TOC_LINE = re.compile(r"(?:\.{3,}|…+|\s)\s*\d{1,4}\s*$")  # «Vacaciones ..... 5»
_PAGE_NUMBER_LINE = re.compile(r"^[.…·\s]*\d{1,4}$")  # «5» o «..... 5»
_LEADER_LINE = re.compile(r"(?:\.{3,}|…+)\s*$")  # «Vacaciones .....»
TOC_MIN_LINES = 5
TOC_MIN_RATIO = 0.4

# Relleno: cuatro o más puntos o puntos medios seguidos, o dos o más puntos suspensivos («…»),
# con o sin espacios entre ellos. Unos puntos suspensivos normales («...») no lo son.
_LEADERS = re.compile(r"[ \t]*(?:(?:[.·][ \t]*){4,}|(?:…[ \t]*){2,})")
_DIGITS = re.compile(r"\d")
_SPACES = re.compile(r"\s+")


def looks_like_toc(text: str) -> bool:
    """True si el texto parece un índice (muchas líneas con número de página o relleno)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < TOC_MIN_LINES:
        return False
    matches = sum(
        1
        for line in lines
        if _TOC_LINE.search(line) or _PAGE_NUMBER_LINE.match(line) or _LEADER_LINE.search(line)
    )
    return matches / len(lines) >= TOC_MIN_RATIO


def _line_key(line: str) -> str:
    """Forma de comparar líneas: sin mayúsculas ni espacios de más y con los dígitos
    igualados (así «Página 3 de 27» y «Página 4 de 27» cuentan como la misma línea)."""
    return _DIGITS.sub("#", _SPACES.sub(" ", line.strip().lower()))


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_repeated_lines(sections: Sequence[Section], min_ratio: float) -> list[Section]:
    """Quita de cada página las líneas que aparecen en al menos `min_ratio` de las páginas.

    Solo se aplica a documentos paginados (PDF) con al menos
    `MIN_PAGES_FOR_REPEATED_LINES` páginas. Si no quedara texto, se devuelven sin cambios.
    """
    if len(sections) < MIN_PAGES_FOR_REPEATED_LINES or any(s.page is None for s in sections):
        return list(sections)
    counts: Counter[str] = Counter()
    for section in sections:
        counts.update({_line_key(line) for line in section.text.splitlines() if line.strip()})
    threshold = max(MIN_PAGES_FOR_REPEATED_LINES, math.ceil(min_ratio * len(sections)))
    repeated = {key for key, count in counts.items() if count >= threshold}
    if not repeated:
        return list(sections)
    result = []
    for section in sections:
        kept = [
            line
            for line in section.text.splitlines()
            if not line.strip() or _line_key(line) not in repeated
        ]
        text = _tidy("\n".join(kept))
        if text:
            result.append(replace(section, text=text))
    return result or list(sections)


def clean_toc_leaders(text: str) -> str:
    """Sustituye los puntos de relleno de los índices por un espacio."""
    return _tidy(_LEADERS.sub(" ", text))


def document_title(filename: str) -> str:
    """Título legible a partir del nombre del fichero (sin extensión ni guiones bajos)."""
    return _SPACES.sub(" ", Path(filename).stem.replace("_", " ")).strip() or filename


def build_chunks(sections: Sequence[Section], indexing: IndexingConfig) -> list[Chunk]:
    """Fragmentos que se indexan: texto preparado, troceado y con la marca de índice."""
    if indexing.strip_repeated_lines:
        sections = strip_repeated_lines(sections, indexing.repeated_line_min_ratio)
    chunks = []
    for chunk in chunk_sections(
        list(sections), indexing.chunk_size_chars, indexing.chunk_overlap_chars
    ):
        # La marca se calcula antes de limpiar el relleno, que es parte de lo que la delata.
        toc = looks_like_toc(chunk.text)
        text = clean_toc_leaders(chunk.text) if indexing.clean_toc_leaders else chunk.text
        chunks.append(replace(chunk, text=text or chunk.text, toc=toc))
    return chunks


def embedding_text(text: str, filename: str, heading: str | None, indexing: IndexingConfig) -> str:
    """Texto con el que se calcula el vector de un fragmento (con su contexto, si procede)."""
    if not indexing.context_prefix:
        return text
    header = [f"Documento: {document_title(filename)}"]
    if heading:
        header.append(f"Sección: {heading}")
    return "\n".join(header) + "\n\n" + text


def embedding_texts(chunks: Sequence[Chunk], filename: str, indexing: IndexingConfig) -> list[str]:
    return [embedding_text(c.text, filename, c.heading, indexing) for c in chunks]


def preprocessing_signature(indexing: IndexingConfig) -> str:
    """Resumen del preprocesado vigente, que se guarda con cada documento indexado."""
    parts = [f"v{PREPROCESSING_VERSION}"]
    if indexing.strip_repeated_lines:
        parts.append(f"lineas={indexing.repeated_line_min_ratio:g}")
    if indexing.clean_toc_leaders:
        parts.append("relleno")
    if indexing.context_prefix:
        parts.append("contexto")
    return ";".join(parts)
