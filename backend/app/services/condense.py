"""Reformulación de preguntas de seguimiento como preguntas independientes.

La búsqueda semántica solo ve la pregunta, no la conversación: «¿y con cuánta
antelación?» no encuentra nada por sí sola. Antes de buscar se pide al modelo que la
reescriba con el contexto necesario. La respuesta final se sigue generando con la
pregunta original del usuario.
"""

import logging
from collections.abc import Sequence

from app.services.llm import ChatMessage, LLMClient, LLMError

logger = logging.getLogger(__name__)

CONDENSE_SYSTEM_PROMPT = """\
Reformula la pregunta de seguimiento como una pregunta independiente, en español, que se \
entienda sin leer la conversación. Incluye los nombres, temas y fechas a los que se refiera. \
Si ya es independiente, devuélvela igual. No la respondas: escribe solo la pregunta."""

# Las respuestas largas del asistente aportan poco para reformular y gastan contexto.
ASSISTANT_EXCERPT_CHARS = 500


def _excerpt(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_condense_messages(previous: Sequence[ChatMessage], question: str) -> list[ChatMessage]:
    """Mensajes para pedir la reformulación."""
    lines = []
    for message in previous:
        if message.role == "user":
            lines.append(f"Usuario: {_excerpt(message.content, 1000)}")
        elif message.role == "assistant":
            lines.append(f"Asistente: {_excerpt(message.content, ASSISTANT_EXCERPT_CHARS)}")
    conversation = "\n".join(lines)
    return [
        ChatMessage(role="system", content=CONDENSE_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"Conversación:\n{conversation}\n\n"
                f"Pregunta de seguimiento: {question}\n\n"
                "Pregunta independiente:"
            ),
        ),
    ]


def _clean(text: str) -> str:
    text = text.strip().strip('"«»“”').strip()
    for prefix in ("Pregunta independiente:", "Pregunta:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    return " ".join(text.split())


async def condense_question(llm: LLMClient, previous: Sequence[ChatMessage], question: str) -> str:
    """Pregunta independiente para la búsqueda; si algo falla, la original."""
    if not any(m.role == "user" for m in previous):
        return question
    try:
        parts = [
            part async for part in llm.stream_chat(build_condense_messages(previous, question))
        ]
    except LLMError:
        logger.warning("No se pudo reformular la pregunta; se busca con la original")
        return question
    condensed = _clean("".join(parts))
    # Respuestas vacías o desproporcionadas (el modelo respondió en vez de reformular).
    if not condensed or len(condensed) > 3 * len(question) + 300:
        return question
    return condensed
