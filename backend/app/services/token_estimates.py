"""Precisión de la estimación del tamaño del prompt (#100) y calibración por modelo (#102).

`prompt_budget.py` estima los tokens a partir de los caracteres, sin el tokenizador del
modelo. Si la estimación se queda corta, el prompt real puede no caber en la ventana y algunos
servidores lo recortan sin avisar. El registro de preguntas guarda lo estimado, lo que contó
el servidor (si lo devuelve) y los caracteres por token con los que se estimó; aquí se
comparan para saber si la estimación es segura con cada modelo y para calibrarla.

Los caracteres por token reales de una pregunta son, aproximadamente, los usados por lo
estimado entre lo contado (el coste fijo por mensaje afecta a los dos recuentos por igual). La
calibración toma un percentil bajo de esos valores, para que la estimación cubra la gran
mayoría de los prompts.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.runtime_config import LlmConfig
from app.db.models import QuestionLog
from app.services.prompt_budget import CHARS_PER_TOKEN, TokenEstimator

logger = logging.getLogger(__name__)

# Proporción de prompts que la estimación debe cubrir para considerarla segura.
SAFE_QUANTILE = 0.95
# Calibración automática (#102): preguntas de los últimos días con recuento del servidor,
# un mínimo de ellas para fiarse, y límites para que unas pocas preguntas raras no lleven
# la estimación a valores absurdos.
CALIBRATION_DAYS = 30
CALIBRATION_MIN_SAMPLES = 30
CALIBRATION_MIN = 2.5
CALIBRATION_MAX = 5.0
# Cuánto se reutiliza la calibración antes de volver a calcularla.
CALIBRATION_CACHE_SECONDS = 3600.0


def quantile(values: Sequence[float], q: float) -> float:
    """Cuantil `q` (0-1) de una lista ordenada, con interpolación lineal."""
    if not values:
        raise ValueError("Sin valores")
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def real_chars_per_token(estimated: int, counted: int, used: float | None) -> float:
    """Caracteres por token reales de una pregunta, a partir de lo estimado con `used`
    caracteres por token (nulo: 3,5, las preguntas anteriores a #102) y lo contado."""
    return (used or CHARS_PER_TOKEN) * estimated / counted


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
    # Caracteres por token con los que la estimación cubriría el 95 % de los prompts.
    safe_chars_per_token: float | None = None

    @property
    def safe(self) -> bool | None:
        """Si la estimación cubre al menos el 95 % de los prompts (`None` sin muestras)."""
        return None if self.p95 is None else self.p95 <= 1


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
            QuestionLog.prompt_chars_per_token,
        ).where(QuestionLog.asked_at >= since, QuestionLog.prompt_tokens_estimated > 0)
    )
    ratios: dict[str, list[float]] = {}
    reals: dict[str, list[float]] = {}
    without_count: dict[str, int] = {}
    over_limit: dict[str, int] = {}
    trimmed: dict[str, int] = {}
    for model, estimated, counted, trim, used in rows:
        name = model or "(sin modelo)"
        ratios.setdefault(name, [])
        reals.setdefault(name, [])
        if trim and trim.get("server"):
            trimmed[name] = trimmed.get(name, 0) + 1
        if not counted:
            without_count[name] = without_count.get(name, 0) + 1
            continue
        ratios[name].append(counted / estimated)
        reals[name].append(real_chars_per_token(estimated, counted, used))
        if limit is not None and counted > limit:
            over_limit[name] = over_limit.get(name, 0) + 1

    estimates = []
    for name, values in ratios.items():
        values.sort()
        chars = sorted(reals[name])
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
                safe_chars_per_token=quantile(chars, 1 - SAFE_QUANTILE) if chars else None,
            )
        )
    estimates.sort(key=lambda e: (-(e.samples + e.without_count), e.model))
    return estimates


# --- Calibración (#102) --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Calibration:
    """Caracteres por token calibrados para un modelo."""

    chars_per_token: float
    # Preguntas en las que se basa; `calibrated` es falso si no llegan al mínimo (se usa 3,5).
    samples: int
    calibrated: bool


async def calibrate(session: AsyncSession, model: str, since: datetime) -> Calibration:
    """Caracteres por token con los que la estimación habría cubierto el 95 % de los prompts
    de `model` desde `since`. No cuenta los prompts que el servidor probablemente recortó (su
    recuento no es el del prompt entero)."""
    rows = await session.execute(
        select(
            QuestionLog.prompt_tokens_estimated,
            QuestionLog.prompt_tokens,
            QuestionLog.prompt_chars_per_token,
            QuestionLog.prompt_trim,
        ).where(
            QuestionLog.model == model,
            QuestionLog.asked_at >= since,
            QuestionLog.prompt_tokens_estimated > 0,
            QuestionLog.prompt_tokens > 0,
        )
    )
    reals = sorted(
        real_chars_per_token(estimated, counted, used)
        for estimated, counted, used, trim in rows
        if not (trim and trim.get("server"))
    )
    if len(reals) < CALIBRATION_MIN_SAMPLES:
        return Calibration(CHARS_PER_TOKEN, len(reals), calibrated=False)
    value = min(CALIBRATION_MAX, max(CALIBRATION_MIN, quantile(reals, 1 - SAFE_QUANTILE)))
    return Calibration(round(value, 2), len(reals), calibrated=True)


class TokenCalibration:
    """Caracteres por token del modelo configurado: el valor fijo de la configuración o,
    con 0, el calibrado (como mucho una vez por hora y modelo)."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        ttl: float = CALIBRATION_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, Calibration]] = {}
        self._lock = asyncio.Lock()

    async def estimator(self, llm: LlmConfig) -> TokenEstimator:
        if llm.chars_per_token:
            return TokenEstimator(llm.chars_per_token)
        return TokenEstimator((await self.calibration(llm.model)).chars_per_token)

    async def calibration(self, model: str) -> Calibration:
        async with self._lock:
            cached = self._cache.get(model)
            if cached and self._clock() - cached[0] < self._ttl:
                return cached[1]
            since = datetime.now(UTC) - timedelta(days=CALIBRATION_DAYS)
            try:
                async with self._sessionmaker() as session:
                    result = await calibrate(session, model, since)
            except Exception:
                # Sin calibración, el chat sigue con el valor por defecto.
                logger.warning("No se pudo calibrar la estimación de tokens", exc_info=True)
                return Calibration(CHARS_PER_TOKEN, 0, calibrated=False)
            if result.calibrated and (not cached or cached[1] != result):
                logger.info(
                    "Estimación de tokens calibrada para %s: %s caracteres por token "
                    "(%d preguntas)",
                    model,
                    result.chars_per_token,
                    result.samples,
                )
            self._cache[model] = (self._clock(), result)
            return result
