"""Detección de los documentos que se nombran en una pregunta (#58).

«¿Qué dice el NOR-013?» o «resúmeme la normativa de control de acceso lógico en DTIC» se
refieren a un documento concreto, pero la búsqueda por similitud no lo sabe: el código o el
título del documento están en su nombre de fichero o en su título (#75). Si la pregunta
nombra un documento, la búsqueda se limita a él.

La coincidencia es estricta para no limitar la búsqueda por error:

- **Códigos**: letras mayúsculas y números del nombre del fichero o del título («NOR-013»,
  «PRO_02», «IT0045»). En la pregunta se aceptan con o sin guion, en minúsculas y sin ceros
  a la izquierda («nor 13»).
- **Títulos**: el nombre del fichero o el título completos, sin la versión («v3.2…») o sin
  el código, con al menos `MIN_TITLE_WORDS` palabras y `MIN_TITLE_CHARS` caracteres, sin
  tener en cuenta mayúsculas ni acentos.

Si coinciden más de `MAX_SCOPE_DOCUMENTS` documentos, la pregunta es ambigua y no se limita.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence

from app.services.ingestion.preprocess import document_title
from app.services.scope import MAX_SCOPE_DOCUMENTS, ScopeDocument

MIN_TITLE_WORDS = 3
MIN_TITLE_CHARS = 15

# Código en el nombre del fichero (mayúsculas): «NOR-013», «PRO_02», «IT0045».
_FILENAME_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})[-_ ]?(\d{2,4})(?![0-9])")
# Código en la pregunta, ya normalizada: «nor 013», «nor013», «nor 13».
_TEXT_CODE = re.compile(r"(?<![a-z0-9])([a-z]{2,6}) ?(\d{1,4})(?![0-9])")
# Inicio de la versión en un título normalizado: «v3 2», «v 3», «version 2», «rev 4».
_VERSION = re.compile(r"\b(?:v ?\d|version \d|rev \d)")


def normalize(text: str) -> str:
    """Minúsculas, sin acentos y solo letras y números separados por un espacio."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def _codes(name: str) -> set[tuple[str, int]]:
    return {(letters.lower(), int(digits)) for letters, digits in _FILENAME_CODE.findall(name)}


def document_codes(filename: str) -> set[tuple[str, int]]:
    """Códigos del nombre del fichero, como (letras, número): «NOR-013» → ("nor", 13)."""
    return _codes(document_title(filename))


def text_codes(text: str) -> set[tuple[str, int]]:
    """Posibles códigos de un texto (sin distinguir mayúsculas ni guiones)."""
    return {(letters, int(digits)) for letters, digits in _TEXT_CODE.findall(normalize(text))}


def _variants(name: str) -> set[str]:
    full = normalize(name)
    variants = {full}
    version = _VERSION.search(full)
    if version:
        variants.add(full[: version.start()].strip())
    for variant in list(variants):
        without_codes = _TEXT_CODE.sub(" ", variant)
        variants.add(" ".join(without_codes.split()))
    return {v for v in variants if len(v) >= MIN_TITLE_CHARS and len(v.split()) >= MIN_TITLE_WORDS}


def title_variants(filename: str) -> set[str]:
    """Formas del nombre del fichero con las que se reconoce el documento en una pregunta."""
    return _variants(document_title(filename))


def _names(document: ScopeDocument, text: str, codes: set[tuple[str, int]]) -> bool:
    """Si el texto nombra el documento, por su nombre de fichero o por su título."""
    names = [document_title(document.filename)]
    if document.title and document.title != document.filename:
        names.append(document.title)
    padded = f" {text} "
    for name in names:
        if _codes(name) & codes:
            return True
        if any(f" {variant} " in padded for variant in _variants(name)):
            return True
    return False


def detect_documents(
    texts: Iterable[str], documents: Sequence[ScopeDocument]
) -> list[ScopeDocument]:
    """Documentos que se nombran en alguno de los textos (la pregunta y su reformulación).

    Devuelve una lista vacía si no se nombra ninguno o si la pregunta es ambigua (más de
    `MAX_SCOPE_DOCUMENTS` documentos).
    """
    found: list[ScopeDocument] = []
    for text in {t for t in texts if t and t.strip()}:
        normalized = normalize(text)
        codes = text_codes(text)
        for document in documents:
            if document not in found and _names(document, normalized, codes):
                found.append(document)
    if len(found) > MAX_SCOPE_DOCUMENTS:
        return []
    return sorted(found, key=lambda d: (d.title or d.filename).casefold())
