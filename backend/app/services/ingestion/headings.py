"""Apartados de los PDF (#74).

Al extraer el texto de un PDF, cada página es una sección sin encabezado: el título del
apartado solo está en el texto. Aquí se detectan las líneas de encabezado y cada página se
divide en ellas, para que cada fragmento sepa a qué apartado pertenece (su `heading`, como
en Markdown, HTML o DOCX: «4. Accesos > 4.2 Altas de usuarios»).

Se reconocen:

- **Numerados**: «4.2 Altas», «4.2. Altas», «4.2 - Altas», o el número en una línea y el
  título en la siguiente (así suele extraerlo pymupdf).
- **Con palabra clave**: «CAPÍTULO V», «Anexo II. Formularios», «Artículo 5. Objeto».

Para no confundirlos con listas numeradas o con líneas que empiezan por una cifra, solo
cuentan los que **siguen la numeración**: de todas las líneas candidatas se elige la cadena
más larga en la que cada número sigue al anterior (tras 4.2 vienen 4.2.1, 4.3 o 5). Una
lista «1. 2. 3.» dentro del apartado 4.2 forma su propia cadena, más corta, y se descarta.
Los anexos reinician la numeración. Las páginas de índice se ignoran.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.ingestion.parsers import Section, heading_path, push_heading

# Longitud máxima de una línea de encabezado (con su número).
MAX_HEADING_CHARS = 120
# Niveles de numeración («4.2.1.3» tiene 4).
MAX_DEPTH = 5

_NUMBER = r"\d{1,2}(?:\.\d{1,2}){0,4}"
# «4.2 Altas», «4.2. Altas», «4.2.- Altas», «4) Altas», «4.2 — Altas» (o con raya corta).
_DASHES = "-" + chr(0x2013) + chr(0x2014)
NUMBERED_LINE = re.compile(rf"^({_NUMBER})(?:\.|\)|\.?\s*[{_DASHES}])?\s+(\S.*)$")
# El número solo: el título va en la línea siguiente.
NUMBER_ONLY_LINE = re.compile(rf"^({_NUMBER})[.)]?$")
KEYWORD_LINE = re.compile(
    r"^(t[íi]tulo|parte|anexo|cap[íi]tulo|secci[óo]n|art[íi]culo)"
    rf"\s+([ivxlcdm]{{1,7}}|\d{{1,3}}|[a-z])(?!\w)\.?\s*(?:[{_DASHES}:.]\s*)?(.*)$",
    re.IGNORECASE,
)
# Final de una línea de índice: número de página tras puntos de relleno o un espacio.
_PAGE_REFERENCE = re.compile(r"(?:\.{3,}|…+|\s)\s*\d{1,4}$")
_SENTENCE_END = (".", ",", ";", ":")

# Nivel de los encabezados con palabra clave; los numerados van por debajo.
KEYWORD_LEVELS = {"titulo": 1, "parte": 1, "anexo": 1, "capitulo": 2, "seccion": 3, "articulo": 4}
NUMBERED_BASE_LEVEL = 4

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(text: str) -> int | None:
    """Valor de un número romano bien formado («XIV» → 14) o `None`."""
    text = text.upper()
    if not text or any(ch not in _ROMAN for ch in text):
        return None
    total = 0
    for index, ch in enumerate(text):
        value = _ROMAN[ch]
        following = _ROMAN[text[index + 1]] if index + 1 < len(text) else 0
        total += -value if value < following else value
    return total if int_to_roman(total) == text else None


def int_to_roman(number: int) -> str:
    """Número romano de un entero positivo (14 → «XIV»)."""
    parts = []
    for value, letters in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):  # fmt: skip
        count, number = divmod(number, value)
        parts.append(letters * count)
    return "".join(parts)


def ordinal_value(text: str) -> int | None:
    """Valor de «5», «V» o «B» (las letras sueltas cuentan como orden alfabético, salvo
    las que también son números romanos de una cifra, que cuentan como romanos)."""
    if text.isdigit():
        return int(text)
    roman = roman_to_int(text)
    if roman is not None:
        return roman
    if len(text) == 1 and "A" <= text.upper() <= "Z":
        return ord(text.upper()) - ord("A") + 1
    return None


def keyword_kind(word: str) -> str:
    """«CAPÍTULO» → «capitulo»."""
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(frozen=True, slots=True)
class Heading:
    """Línea de encabezado candidata."""

    section: int
    line: int
    # Líneas que ocupa (2 si el número y el título van en líneas distintas).
    lines: int
    text: str
    level: int
    # Numerados: el número («4.2» → (4, 2)); con palabra clave: (clase, valor).
    number: tuple[int, ...] = ()
    kind: str = ""
    value: int = 0


def _plausible_title(title: str) -> bool:
    title = title.strip()
    return (
        bool(title)
        and title[0].isalpha()
        and title[0].isupper()
        and not title.endswith(_SENTENCE_END)
        and not _PAGE_REFERENCE.search(title)
    )


def _numbered(section: int, index: int, lines: list[str]) -> Heading | None:
    line = lines[index]
    used = 1
    match = NUMBERED_LINE.match(line)
    if match:
        number, title = match.groups()
    else:
        only = NUMBER_ONLY_LINE.match(line)
        if only is None or index + 1 >= len(lines):
            return None
        number, title, used = only.group(1), lines[index + 1], 2
    if not _plausible_title(title) or len(number) + len(title) + 1 > MAX_HEADING_CHARS:
        return None
    values = tuple(int(part) for part in number.split("."))
    if len(values) > MAX_DEPTH or values[0] == 0:
        return None
    return Heading(
        section,
        index,
        used,
        f"{number} {title.strip()}",
        NUMBERED_BASE_LEVEL + len(values),
        number=values,
    )


def _keyword(section: int, index: int, lines: list[str]) -> Heading | None:
    match = KEYWORD_LINE.match(lines[index])
    if match is None or len(lines[index]) > MAX_HEADING_CHARS:
        return None
    word, ordinal, title = match.groups()
    value = ordinal_value(ordinal)
    # En mayúscula inicial y sin continuar una frase («Artículo 5 de la Ley…»).
    if value is None or not word[0].isupper():
        return None
    if title and (title[0].islower() or _PAGE_REFERENCE.search(title)):
        return None
    kind = keyword_kind(word)
    text = lines[index]
    used = 1
    # «ANEXO II» y el título en la línea siguiente.
    if not title and index + 1 < len(lines) and _plausible_title(lines[index + 1]):
        following = lines[index + 1]
        if (
            len(text) + len(following) + 2 <= MAX_HEADING_CHARS
            and not NUMBERED_LINE.match(following)
            and not KEYWORD_LINE.match(following)
        ):
            text, used = f"{text}. {following}", 2
    return Heading(section, index, used, text, KEYWORD_LEVELS[kind], kind=kind, value=value)


def _candidates(sections: Sequence[Section], skip: set[int]) -> list[Heading]:
    found = []
    for number, section in enumerate(sections):
        if number in skip:
            continue
        lines = [line.strip() for line in section.text.splitlines()]
        index = 0
        while index < len(lines):
            heading = (
                (_keyword(number, index, lines) or _numbered(number, index, lines))
                if lines[index]
                else None
            )
            if heading:
                found.append(heading)
                index += heading.lines
            else:
                index += 1
    return found


def _follows(previous: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """`current` es el apartado siguiente a `previous`: su primer subapartado o el
    siguiente de alguno de sus niveles (4.2 → 4.2.1, 4.3, 5)."""
    if current == (*previous, 1):
        return True
    return any(
        current == (*previous[:level], previous[level] + 1) for level in range(len(previous))
    )


def _starts(number: tuple[int, ...]) -> bool:
    """Puede empezar una numeración (el primer apartado puede ser cualquiera de nivel 1,
    o el primero de su nivel)."""
    return len(number) == 1 or number[-1] == 1


def _longest_chain(candidates: list[Heading]) -> list[Heading]:
    """La cadena más larga de numerados en la que cada uno sigue al anterior."""
    best: list[int] = [0] * len(candidates)
    previous: list[int | None] = [None] * len(candidates)
    for j, heading in enumerate(candidates):
        best[j] = 1 if _starts(heading.number) else 0
        for i in range(j):
            if best[i] and best[i] + 1 > best[j] and _follows(candidates[i].number, heading.number):
                best[j] = best[i] + 1
                previous[j] = i
    if not candidates or max(best) == 0:
        return []
    end: int | None = max(range(len(candidates)), key=lambda k: best[k])
    chain = []
    while end is not None:
        chain.append(candidates[end])
        end = previous[end]
    return chain[::-1]


def _keyword_chain(candidates: list[Heading]) -> list[Heading]:
    """Encabezados con palabra clave cuya numeración aumenta, por clase («Capítulo I, II…»).

    Un artículo puede citar otro a principio de línea («Artículo 5 de la Ley…»): solo
    cuentan los que siguen el orden de los de su clase.
    """
    accepted = []
    last: dict[str, int] = {}
    for heading in candidates:
        expected = last.get(heading.kind, 0) + 1
        if heading.value == expected or (heading.kind not in last and heading.value <= 2):
            accepted.append(heading)
            last[heading.kind] = heading.value
            # Un capítulo nuevo no reinicia los artículos (la numeración es continua); un
            # anexo o un título reinician las secciones interiores.
            if heading.kind in ("anexo", "titulo", "parte"):
                for inner in ("capitulo", "seccion"):
                    last.pop(inner, None)
    return accepted


def detect_headings(sections: Sequence[Section], skip: set[int]) -> list[Heading]:
    """Encabezados de las páginas (salvo las de `skip`), en orden de aparición."""
    candidates = _candidates(sections, skip)
    keywords = _keyword_chain([c for c in candidates if c.kind])
    # Cada anexo (o título) reinicia la numeración: se busca la cadena en cada tramo.
    resets = [h for h in keywords if h.kind in ("anexo", "titulo", "parte")]
    numbered = [c for c in candidates if not c.kind]
    accepted = list(keywords)
    bounds = [(h.section, h.line) for h in resets]
    start: tuple[int, int] = (-1, -1)
    for stop in [*bounds, (len(sections), 0)]:
        segment = [c for c in numbered if start <= (c.section, c.line) < stop]
        accepted += _longest_chain(segment)
        start = stop
    return sorted(accepted, key=lambda h: (h.section, h.line))


def split_by_headings(sections: Sequence[Section], toc_pages: set[int]) -> list[Section]:
    """Divide las páginas en sus encabezados: cada trozo lleva la ruta de los apartados
    abiertos (que pasa de una página a las siguientes) y empieza por su encabezado."""
    headings = detect_headings(sections, toc_pages)
    if not headings:
        return list(sections)
    starts: dict[tuple[int, int], Heading] = {(h.section, h.line): h for h in headings}
    stack: list[tuple[int, str]] = []
    result: list[Section] = []
    for number, section in enumerate(sections):
        lines = section.text.splitlines()
        current: list[str] = []
        for index, line in enumerate(lines):
            heading = starts.get((number, index))
            if heading is not None:
                text = "\n".join(current).strip()
                if text:
                    result.append(Section(text, section.page, heading_path(stack)))
                current = []
                push_heading(stack, heading.level, heading.text)
            current.append(line)
        text = "\n".join(current).strip()
        if text:
            result.append(Section(text, section.page, heading_path(stack)))
    return result
