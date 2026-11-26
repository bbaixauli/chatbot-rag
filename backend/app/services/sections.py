"""Apartados que se citan en una pregunta (#74).

«¿Qué dice el apartado 4.2 del NOR-013?» o «resume la sección de altas de usuarios» se
refieren a un apartado concreto, pero la búsqueda por similitud no lo sabe: el número del
apartado apenas cambia el vector de la pregunta, y el fragmento que lo contiene puede
quedarse fuera. Cuando la búsqueda está limitada a unos documentos, se buscan sus
encabezados y se añade el apartado completo (hasta el siguiente del mismo nivel o superior).

Referencias que se reconocen (sin distinguir mayúsculas ni acentos):

- **Por número**, siempre con una palabra clave delante: «apartado 4.2», «punto 3»,
  «sección 2.1.3», «capítulo 5» (o «capítulo V»), «anexo II», «artículo 12», «epígrafe 4»,
  «cláusula 7». Un número suelto («4.2 millones») no cuenta.
- **Por título**: entre comillas («…», "…") o en la forma «el apartado / la sección / el
  capítulo / el anexo / el epígrafe / el punto de X». El título debe coincidir con un encabezado del
  documento, así que «el punto de vista» no añade nada.

Los encabezados son los `heading` de los fragmentos (Markdown, HTML, Office y los PDF
indexados con la detección de apartados) o, si el documento no tiene ninguno, las líneas
de encabezado de su texto (PDF indexados antes de #74). Los fragmentos de índice no cuentan.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.services.document_names import normalize
from app.services.ingestion.headings import (
    KEYWORD_LINE,
    NUMBER_ONLY_LINE,
    NUMBERED_LINE,
    detect_headings,
    keyword_kind,
    ordinal_value,
)
from app.services.ingestion.parsers import Section
from app.services.vectorstore import StoredChunk

# Palabras clave (sin acentos) y si admiten un número romano o una letra («anexo B»).
_REFERENCE_WORDS = (
    r"apartados?|puntos?|secci[oó]n(?:es)?|cap[ií]tulos?|ep[ií]grafes?|art[ií]culos?"
    r"|cl[aá]usulas?|anexos?|t[ií]tulos?"
)
_ORDINAL = r"\d{1,2}(?:\.\d{1,2}){0,4}|[IVXLCDM]{1,7}|[ivxlcdm]{1,7}|[A-Z]"
_BY_NUMBER = re.compile(
    rf"(?<![\w])({_REFERENCE_WORDS})\s+(?:(?:n\.?\s*[º°o]|n[úu]m(?:ero)?)\.?\s*)?"
    rf"((?:{_ORDINAL})(?:\s*(?:,|y|e)\s*(?:{_ORDINAL}))*)(?![\w]|\.\d)",
    re.IGNORECASE,
)
_ORDINAL_ITEM = re.compile(_ORDINAL)
_BY_TITLE = re.compile(
    r"(?<![\w])(?:el|la|del|al)\s+(apartado|punto|secci[oó]n|cap[ií]tulo|anexo|ep[ií]grafe)"
    r"\s+(?:de|sobre)\s+([^?¿!¡.,;:()«»\"“”]+)",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[«\"“]([^«»\"“”]{4,120})[»\"”]")
# Palabras que suelen seguir al título en la pregunta («… del NOR-013», «… en la norma»).
_TITLE_END = re.compile(r"\s+(?:del|en|según|segun|para|que)\s.*$", re.IGNORECASE)
MIN_TITLE_CHARS = 4
# Palabras clave que admiten letras y romanos en minúscula («capítulo v», «anexo ii»).
_LOWER_ORDINALS = ("capitulo", "anexo", "titulo")


@dataclass(frozen=True, slots=True)
class SectionReference:
    """Apartado citado en la pregunta."""

    # Cómo se cita («apartado 4.2», «sección de altas»), para el diagnóstico y el log.
    text: str
    # Palabra clave sin acentos ni plural («apartado», «capitulo»…).
    kind: str
    # Por número: «4.2» → (4, 2); «V» → (5,). Por título: vacío.
    number: tuple[int, ...] = ()
    # Por título: normalizado (ver `normalize`).
    title: str = ""


def _singular(word: str) -> str:
    kind = keyword_kind(word)
    if kind.endswith("es") and kind[:-2] in ("seccion",):
        return kind[:-2]
    return kind.removesuffix("s")


def _ordinal_number(text: str, kind: str) -> tuple[int, ...] | None:
    if text[0].isdigit():
        return tuple(int(part) for part in text.split("."))
    if text.islower() and kind not in _LOWER_ORDINALS:
        return None
    value = ordinal_value(text)
    return (value,) if value else None


def detect_references(texts: Iterable[str]) -> list[SectionReference]:
    """Apartados citados en los textos (la pregunta y su reformulación), sin repetir."""
    found: dict[tuple[str, tuple[int, ...], str], SectionReference] = {}
    for text in texts:
        for match in _BY_NUMBER.finditer(text):
            kind = _singular(match.group(1))
            for item in _ORDINAL_ITEM.findall(match.group(2)):
                number = _ordinal_number(item, kind)
                if number:
                    reference = SectionReference(f"{kind} {item}", kind, number=number)
                    found.setdefault((kind, number, ""), reference)
        titles = [
            (_singular(m.group(1)), _TITLE_END.sub("", m.group(2).strip()))
            for m in _BY_TITLE.finditer(text)
        ]
        titles += [("apartado", m.group(1)) for m in _QUOTED.finditer(text)]
        for kind, raw in titles:
            title = normalize(raw)
            if len(title) >= MIN_TITLE_CHARS:
                found.setdefault(
                    ("", (), title), SectionReference(f"{kind} de «{title}»", kind, title=title)
                )
    return list(found.values())


@dataclass(frozen=True, slots=True)
class HeadingKey:
    """Lo que identifica un encabezado: su número, su palabra clave y su título."""

    number: tuple[int, ...] = ()
    kind: str = ""
    title: str = ""


def heading_key(text: str) -> HeadingKey:
    """«4.2 Altas de usuarios» → número (4, 2) y título «altas de usuarios»;
    «CAPÍTULO V. Accesos» → clase «capitulo», número (5,) y título «accesos»."""
    text = text.strip()
    keyword = KEYWORD_LINE.match(text)
    if keyword:
        word, ordinal, title = keyword.groups()
        value = ordinal_value(ordinal)
        if value:
            return HeadingKey((value,), keyword_kind(word), normalize(title))
    numbered = NUMBERED_LINE.match(text) or NUMBER_ONLY_LINE.match(text.split("\n")[0])
    if numbered:
        number = tuple(int(part) for part in numbered.group(1).split("."))
        title = numbered.group(2) if numbered.re is NUMBERED_LINE else ""
        return HeadingKey(number, title=normalize(title))
    return HeadingKey(title=normalize(text))


def _title_matches(reference: str, heading: str) -> bool:
    """El título citado está en el del encabezado (o al revés, si se citó con más palabras:
    «la sección de altas de usuarios en el directorio»), palabra a palabra."""
    if not heading:
        return False
    padded = f" {heading} "
    if f" {reference} " in padded:
        return True
    words = reference.split()
    # Se quitan palabras del final mientras quede un título reconocible.
    while len(words) > 1:
        words.pop()
        shorter = " ".join(words)
        if len(shorter) >= MIN_TITLE_CHARS and f" {shorter} " in padded:
            return len(shorter) >= len(heading) / 2
    return False


def matches(reference: SectionReference, key: HeadingKey) -> bool:
    """Si el encabezado es el apartado citado."""
    if reference.title:
        return _title_matches(reference.title, key.title)
    if not key.number or key.number != reference.number:
        return False
    # «Capítulo 5» es «CAPÍTULO V» o el apartado «5 …»; «apartado 4.2», el «4.2 …»; un
    # artículo o un anexo, solo uno con su palabra clave.
    if key.kind:
        return key.kind == reference.kind or (
            reference.kind in ("apartado", "punto", "seccion", "epigrafe", "clausula")
            and key.kind in ("seccion", "articulo")
        )
    return reference.kind not in ("articulo", "anexo")


@dataclass(frozen=True, slots=True)
class HeadingStart:
    """Dónde empieza un apartado: fragmento, nivel y texto del encabezado."""

    position: int
    level: int
    text: str
    # El encabezado está al principio del fragmento (el anterior no tiene nada suyo).
    at_start: bool = True


def _metadata_headings(chunks: Sequence[StoredChunk]) -> list[HeadingStart]:
    """Encabezados según la sección guardada de cada fragmento («A > B > C»)."""
    starts: list[HeadingStart] = []
    previous: list[str] = []
    for position, chunk in enumerate(chunks):
        path = [part.strip() for part in (chunk.heading or "").split(" > ") if part.strip()]
        common = 0
        while common < min(len(path), len(previous)) and path[common] == previous[common]:
            common += 1
        for level in range(common, len(path)):
            if not chunk.toc:
                starts.append(HeadingStart(position, level + 1, path[level]))
        previous = path
    return starts


def _text_headings(chunks: Sequence[StoredChunk]) -> list[HeadingStart]:
    """Encabezados según las líneas del texto (documentos sin secciones guardadas)."""
    sections = [Section(chunk.text) for chunk in chunks]
    skip = {position for position, chunk in enumerate(chunks) if chunk.toc}
    return [
        HeadingStart(h.section, h.level, h.text, at_start=h.line == 0)
        for h in detect_headings(sections, skip)
    ]


def document_headings(chunks: Sequence[StoredChunk]) -> list[HeadingStart]:
    """Encabezados de un documento, en orden."""
    if any(chunk.heading for chunk in chunks):
        return _metadata_headings(chunks)
    return _text_headings(chunks)


@dataclass(frozen=True, slots=True)
class LocatedSection:
    """Apartado localizado en un documento: sus fragmentos, en orden."""

    reference: SectionReference
    heading: str
    chunks: list[StoredChunk]


def locate(
    references: Sequence[SectionReference], chunks: Sequence[StoredChunk]
) -> list[LocatedSection]:
    """Apartados citados en un documento (sus fragmentos, ordenados por índice): cada uno,
    desde su encabezado hasta el siguiente del mismo nivel o superior."""
    if not references or not chunks:
        return []
    headings = document_headings(chunks)
    keys = [heading_key(h.text) for h in headings]
    located: list[LocatedSection] = []
    taken: set[int] = set()
    for reference in references:
        found = next(
            (i for i, key in enumerate(keys) if matches(reference, key)),
            None,
        )
        if found is None:
            continue
        start = headings[found]
        end = len(chunks)
        for following in headings[found + 1 :]:
            if following.level <= start.level and following.position > start.position:
                # El fragmento del siguiente encabezado se incluye si este no está a su
                # principio: tiene el final del apartado.
                end = following.position + (0 if following.at_start else 1)
                break
            if following.level <= start.level:
                end = start.position + 1
                break
        end = max(end, start.position + 1)
        positions = [p for p in range(start.position, end) if p not in taken]
        if not positions:
            continue
        taken.update(positions)
        located.append(LocatedSection(reference, start.text, [chunks[p] for p in positions]))
    return located
