"""Precisión de la estimación del tamaño del prompt, por modelo (#100).

`prompt_budget.py` estima los tokens a partir de los caracteres, sin el tokenizador del
modelo. Si la estimación se queda corta, el prompt real puede no caber en la ventana y algunos
servidores lo recortan sin avisar. El registro de preguntas guarda lo estimado y lo que contó
el servidor (si lo devuelve); aquí se comparan para saber si la estimación es segura con cada
modelo.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import QuestionLog
from app.services.prompt_budget import CHARS_PER_TOKEN

# Proporción de prompts que la estimación debe cubrir para considerarla segura.
SAFE_QUANTILE = 0.95


def quantile(values: Sequence[float], q: float) -> float:
    """Cuantil `q` (0-1) de una lista ordenada, con interpolación lineal."""
    if not values:
        raise ValueError("Sin valores")
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


@dataclass(frozen=True, slots=True)
class ModelEstimate:
    """Cómo se compara lo estimado con lo que contó el servidor para un modelo."""

    model: str
    # Preguntas con los dos recuentos y preguntas con solo la estimación (el servidor no
    # devolvió el recuento: no admite `stream_options` o falló antes de terminar).
    samples: int
    without_count: int
    # Relación real / estimado: por encima de 1, la estimación se quedó corta.
    median: float | None
    p95: float | None
    max: float | None
    underestimated: int
    # Prompts reales por encima del límite indicado (el vigente; no se guarda el de cada
    # pregunta) y recortes que detectó el chat al comparar con el recuento del servidor.
    over_limit: int
    server_trimmed: int

    @property
    def safe(self) -> bool | None:
        """Si la estimación cubre al menos el 95 % de los prompts (`None` sin muestras)."""
        return None if self.p95 is None else self.p95 <= 1

    @property
    def safe_chars_per_token(self) -> float | None:
        """Caracteres por token con los que la estimación cubriría el 95 % de los prompts."""
        return None if not self.p95 else CHARS_PER_TOKEN / self.p95


async def model_estimates(
    session: AsyncSession, since: datetime, limit: int | None = None
) -> list[ModelEstimate]:
    """Precisión de la estimación de cada modelo en las preguntas desde `since`, ordenados
    por número de preguntas. `limit`: tokens que puede ocupar el prompt (la ventana menos la
    reserva para la respuesta), si está configurada."""
    rows = await session.execute(
        select(
            QuestionLog.model,
            QuestionLog.prompt_tokens_estimated,
            QuestionLog.prompt_tokens,
            QuestionLog.prompt_trim,
        ).where(QuestionLog.asked_at >= since, QuestionLog.prompt_tokens_estimated > 0)
    )
    ratios: dict[str, list[float]] = {}
    without_count: dict[str, int] = {}
    over_limit: dict[str, int] = {}
    trimmed: dict[str, int] = {}
    for model, estimated, counted, trim in rows:
        name = model or "(sin modelo)"
        ratios.setdefault(name, [])
        if trim and trim.get("server"):
            trimmed[name] = trimmed.get(name, 0) + 1
        if not counted:
            without_count[name] = without_count.get(name, 0) + 1
            continue
        ratios[name].append(counted / estimated)
        if limit is not None and counted > limit:
            over_limit[name] = over_limit.get(name, 0) + 1

    estimates = []
    for name, values in ratios.items():
        values.sort()
        estimates.append(
            ModelEstimate(
                model=name,
                samples=len(values),
                without_count=without_count.get(name, 0),
                median=quantile(values, 0.5) if values else None,
                p95=quantile(values, SAFE_QUANTILE) if values else None,
                max=values[-1] if values else None,
                underestimated=sum(1 for value in values if value > 1),
                over_limit=over_limit.get(name, 0),
                server_trimmed=trimmed.get(name, 0),
            )
        )
    estimates.sort(key=lambda e: (-(e.samples + e.without_count), e.model))
    return estimates
