"""Unión de fragmentos consecutivos de un documento en un pasaje (#58).

Cada fragmento repite al principio el final del anterior (el solapamiento del troceado), así
que al unirlos hay que quitar lo repetido para no enviar el mismo texto dos veces al modelo.
"""

from collections.abc import Sequence

# Solapamiento mínimo que se reconoce: por debajo, una coincidencia puede ser casual (un
# punto, una palabra corta) y se unen los textos tal cual.
MIN_OVERLAP_CHARS = 20


def overlap_length(previous: str, following: str) -> int:
    """Caracteres del principio de `following` que repiten el final de `previous`."""
    for length in range(min(len(previous), len(following)), MIN_OVERLAP_CHARS - 1, -1):
        if previous.endswith(following[:length]):
            return length
    return 0


def join_chunks(texts: Sequence[str]) -> str:
    """Une los textos de fragmentos consecutivos sin repetir el solapamiento."""
    if not texts:
        return ""
    joined = texts[0]
    for text in texts[1:]:
        rest = text[overlap_length(joined, text) :].strip()
        if rest:
            joined = f"{joined}\n\n{rest}"
    return joined


def contiguous_runs(indices: Sequence[int]) -> list[list[int]]:
    """Agrupa índices en tramos consecutivos: [1, 2, 3, 7, 8] → [[1, 2, 3], [7, 8]]."""
    runs: list[list[int]] = []
    for index in sorted(set(indices)):
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs
