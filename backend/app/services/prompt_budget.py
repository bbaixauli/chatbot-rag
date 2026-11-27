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

La estimación no depende del tokenizador de cada modelo: caracteres entre caracteres por token,
más un coste fijo por mensaje (la plantilla del chat). Los caracteres por token son 3,5 (algo
conservador en español), un valor fijo de la configuración o el calibrado para el modelo con
el recuento del servidor (#102, ver `token_estimates.py`).
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
# que haya recortado el prompt (la estimación ya es conservadora: suele contar ~0,8). Así se
# detecta una ventana configurada mayor que la del servidor.
SERVER_TRIM_RATIO = 0.6
# Si el servidor cuenta al menos esta proporción de la ventana, lo más probable es que haya
# recortado el prompt al tope de la ventana: la estimación se quedó corta y el prompt real no
# cabía (#101). La comparación anterior no lo detecta, porque lo contado (≈ la ventana) supera
# lo estimado.
WINDOW_FULL_RATIO = 0.98


@dataclass(frozen=True, slots=True)
class TokenEstimator:
    """Estima tokens a partir de los caracteres, con unos caracteres por token dados."""

    chars_per_token: float = CHARS_PER_TOKEN

    def chars_to_tokens(self, chars: int) -> int:
        return math.ceil(chars / self.chars_per_token)

    def estimate_tokens(self, text: str) -> int:
        return self.chars_to_tokens(len(text))

    def message_tokens(self, message: ChatMessage) -> int:
        return self.estimate_tokens(message.content) + MESSAGE_OVERHEAD_TOKENS

    def messages_tokens(self, messages: Sequence[ChatMessage]) -> int:
        return sum(self.message_tokens(message) for message in messages)

    def tokens_to_chars(self, tokens: int) -> int:
        """Caracteres que caben en `tokens` (0 si no queda sitio)."""
        return max(0, math.floor(tokens * self.chars_per_token))

    def documents_budget(self, limit: int, used_tokens: int) -> int:
        """Caracteres que puede ocupar la documentación si el resto del prompt ocupa
        `used_tokens` (incluye las instrucciones con las que se presenta)."""
        return self.tokens_to_chars(limit - used_tokens - self.estimate_tokens(RAG_INSTRUCTIONS))

    def fit_recent(
        self, messages: Sequence[ChatMessage], budget: int
    ) -> tuple[list[ChatMessage], int]:
        """Los mensajes más recientes que caben en `budget` tokens, en orden cronológico, y
        cuántos se quedan fuera (los más antiguos)."""
        kept: list[ChatMessage] = []
        used = 0
        for message in reversed(messages):
            cost = self.message_tokens(message)
            if used + cost > budget:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        return kept, len(messages) - len(kept)


# Con los caracteres por token por defecto (los avisos de la configuración, `diagnose`).
DEFAULT_ESTIMATOR = TokenEstimator()
chars_to_tokens = DEFAULT_ESTIMATOR.chars_to_tokens
estimate_tokens = DEFAULT_ESTIMATOR.estimate_tokens
message_tokens = DEFAULT_ESTIMATOR.message_tokens
messages_tokens = DEFAULT_ESTIMATOR.messages_tokens
tokens_to_chars = DEFAULT_ESTIMATOR.tokens_to_chars
documents_budget = DEFAULT_ESTIMATOR.documents_budget
fit_recent = DEFAULT_ESTIMATOR.fit_recent


@dataclass(slots=True)
class PromptTrim:
    """Lo que se ha quitado del prompt para que quepa en la ventana."""

    # Se iba a enviar el documento completo y se enviaron fragmentos.
    full_document: bool = False
    # Fragmentos que se habrían usado y no cabían.
    sources: int = 0
    # Mensajes del historial que no se enviaron.
    history: int = 0
    # Por el recuento del servidor, lo más probable es que recortara el prompt: contó bastantes
    # menos tokens de los estimados o llenó la ventana (ver `server_trimmed`).
    server: bool = False
    # El servidor contó más tokens que el límite (ventana menos la reserva para la respuesta):
    # la estimación se quedó corta y el prompt ocupó parte de la reserva o no cabía (#101).
    underestimated: bool = False

    @property
    def payload(self) -> dict[str, Any] | None:
        """Para el registro de preguntas (`None` si no se recortó nada)."""
        values = {
            "full_document": self.full_document,
            "sources": self.sources,
            "history": self.history,
            "server": self.server,
            "underestimated": self.underestimated,
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


def server_trimmed(estimated: int, counted: int | None, window: int | None = None) -> bool:
    """Si, por el recuento del servidor, lo más probable es que recortara el prompt: contó
    bastantes menos tokens de los estimados o, con la ventana configurada, la llenó."""
    if counted is None:
        return False
    if window and counted >= window * WINDOW_FULL_RATIO:
        return True
    return estimated > 0 and counted < estimated * SERVER_TRIM_RATIO


def underestimated(counted: int | None, limit: int | None) -> bool:
    """Si el servidor contó más tokens que el límite del prompt (la estimación se quedó
    corta). Sin ventana configurada (`limit` es `None`) no hay con qué comparar."""
    return counted is not None and limit is not None and counted > limit
