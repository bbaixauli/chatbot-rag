"""Tamaño del prompt frente a la ventana de contexto del modelo (#73).

Algunos servidores (Ollama con su `num_ctx`, por ejemplo) recortan sin error el prompt que no
cabe en la ventana, y lo hacen por el principio: se pierde el prompt de sistema con la
documentación. Con `llm.context_window_tokens` se estima el tamaño del prompt y se ajusta antes
de enviarlo, en este orden:

1. El documento completo (#58) se sustituye por sus fragmentos más parecidos.
2. Se quita el historial antiguo, conservando el último intercambio.
3. Se quitan los fragmentos de menor puntuación.
4. Se quita el resto del historial.
5. Si ni el prompt de sistema y la pregunta caben, no se envía (`PromptTooLargeError`).

La estimación es conservadora y no depende del tokenizador de cada modelo: unos 3,5 caracteres
por token en español, más un coste fijo por mensaje (la plantilla del chat).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.services.llm import ChatMessage
from app.services.rag import RAG_INSTRUCTIONS

CHARS_PER_TOKEN = 3.5
MESSAGE_OVERHEAD_TOKENS = 8
# Si el servidor cuenta menos de esta proporción de los tokens estimados, lo más probable es
# que haya recortado el prompt (la estimación ya es conservadora: suele contar ~0,8).
SERVER_TRIM_RATIO = 0.6


def chars_to_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_tokens(text: str) -> int:
    return chars_to_tokens(len(text))


def message_tokens(message: ChatMessage) -> int:
    return estimate_tokens(message.content) + MESSAGE_OVERHEAD_TOKENS


def messages_tokens(messages: Sequence[ChatMessage]) -> int:
    return sum(message_tokens(message) for message in messages)


def tokens_to_chars(tokens: int) -> int:
    """Caracteres que caben en `tokens` (0 si no queda sitio)."""
    return max(0, math.floor(tokens * CHARS_PER_TOKEN))


def documents_budget(limit: int, used_tokens: int) -> int:
    """Caracteres que puede ocupar la documentación si el resto del prompt ocupa
    `used_tokens` (incluye las instrucciones con las que se presenta)."""
    return tokens_to_chars(limit - used_tokens - estimate_tokens(RAG_INSTRUCTIONS))


def fit_recent(messages: Sequence[ChatMessage], budget: int) -> tuple[list[ChatMessage], int]:
    """Los mensajes más recientes que caben en `budget` tokens, en orden cronológico, y
    cuántos se quedan fuera (los más antiguos)."""
    kept: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = message_tokens(message)
        if used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept, len(messages) - len(kept)


@dataclass(slots=True)
class PromptTrim:
    """Lo que se ha quitado del prompt para que quepa en la ventana."""

    # Se iba a enviar el documento completo y se enviaron fragmentos.
    full_document: bool = False
    # Fragmentos que se habrían usado y no cabían.
    sources: int = 0
    # Mensajes del historial que no se enviaron.
    history: int = 0
    # El servidor contó bastantes menos tokens de los estimados: recortó el prompt.
    server: bool = False

    @property
    def payload(self) -> dict[str, Any] | None:
        """Para el registro de preguntas (`None` si no se recortó nada)."""
        values = {
            "full_document": self.full_document,
            "sources": self.sources,
            "history": self.history,
            "server": self.server,
        }
        present = {key: value for key, value in values.items() if value}
        return present or None


class PromptTooLargeError(Exception):
    """Ni el prompt de sistema y la pregunta caben en la ventana del modelo."""

    def __init__(self, estimated: int, limit: int) -> None:
        super().__init__(
            f"El prompt mínimo (prompt de sistema y pregunta) ocupa unos {estimated} tokens y "
            f"el límite es {limit} (ventana de contexto menos la reserva para la respuesta)"
        )
        self.estimated = estimated
        self.limit = limit


def server_trimmed(estimated: int, counted: int | None) -> bool:
    """Si el servidor contó bastantes menos tokens de los estimados."""
    return counted is not None and estimated > 0 and counted < estimated * SERVER_TRIM_RATIO
