"""Cuadro de mando (#51): registro de preguntas, estadísticas y exportación.

- Cada pregunta queda en `question_log` con sus textos y tiempos, también si falla o se
  cancela, y aunque después se borre la conversación.
- Una sesión es la sesión de navegador del chat; solo cuentan las que han preguntado.
- Una respuesta «no tiene la información» si contiene alguna de las frases configuradas
  (sin distinguir mayúsculas ni tildes). Al cambiar las frases se recalcula todo el registro.
- Los días se cuentan en la zona horaria de la aplicación (`CHATBOT_TIMEZONE`).
"""

import csv
import io
import logging
import statistics
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppSetting, Conversation, QuestionLog, QuestionStatus, utcnow

logger = logging.getLogger(__name__)

SETTINGS_KEY = "dashboard"
MAX_RANGE_DAYS = 366
MAX_PHRASES = 50
MAX_PHRASE_LENGTH = 200
RECOMPUTE_BATCH = 1000

# Frases con las que el modelo suele indicar que la documentación no tiene la respuesta
# (el prompt del RAG le pide decirlo claramente).
DEFAULT_NO_INFO_PHRASES = [
    "no tengo información",
    "no dispongo de información",
    "no encuentro información",
    "no he encontrado información",
    "no hay información",
    "no se menciona",
    "no se indica",
    "no contienen información",
    "no contienen la respuesta",
    "no aparece en la documentación",
    "no puedo responder",
    "no tengo datos",
]


class DashboardError(ValueError):
    """Parámetro del cuadro de mando no válido."""


# --- Detección de «sin información» ---


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y con los espacios simplificados."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.split())


def says_no_info(answer: str | None, phrases: Iterable[str]) -> bool:
    if not answer:
        return False
    text = normalize(answer)
    return any(normalize(phrase) in text for phrase in phrases if phrase.strip())


def clean_phrases(phrases: Sequence[str]) -> list[str]:
    """Frases sin repetir ni vacías, con límites de cantidad y longitud."""
    result: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        text = " ".join(str(phrase).split())
        if not text:
            continue
        if len(text) > MAX_PHRASE_LENGTH:
            raise DashboardError(f"Cada frase admite como máximo {MAX_PHRASE_LENGTH} caracteres")
        key = normalize(text)
        if key not in seen:
            seen.add(key)
            result.append(text)
    if len(result) > MAX_PHRASES:
        raise DashboardError(f"Como máximo {MAX_PHRASES} frases")
    return result


class DashboardSettingsRepository:
    """Frases de «sin información» (en `app_settings`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def phrases(self) -> list[str]:
        setting = await self._session.get(AppSetting, SETTINGS_KEY)
        if setting is None:
            return list(DEFAULT_NO_INFO_PHRASES)
        return [str(p) for p in setting.value.get("no_info_phrases", [])]

    async def save_phrases(self, phrases: Sequence[str], author: str) -> list[str]:
        """Guarda las frases y vuelve a clasificar todo el registro."""
        cleaned = clean_phrases(phrases)
        value = {"no_info_phrases": cleaned}
        setting = await self._session.get(AppSetting, SETTINGS_KEY)
        if setting is None:
            self._session.add(AppSetting(key=SETTINGS_KEY, value=value, updated_by=author))
        else:
            setting.value = value
            setting.updated_by = author
            setting.updated_at = utcnow()
        await self._session.flush()
        await recompute_no_info(self._session, cleaned)
        return cleaned


async def recompute_no_info(session: AsyncSession, phrases: Sequence[str]) -> int:
    """Vuelve a clasificar las respuestas del registro. Devuelve cuántas cambiaron."""
    changed = 0
    last_id = 0
    while True:
        rows = (
            await session.execute(
                select(QuestionLog.id, QuestionLog.answer, QuestionLog.no_info)
                .where(QuestionLog.id > last_id)
                .order_by(QuestionLog.id)
                .limit(RECOMPUTE_BATCH)
            )
        ).all()
        if not rows:
            return changed
        last_id = rows[-1].id
        for row_id, answer, current in rows:
            flag = says_no_info(answer, phrases)
            if flag != current:
                await session.execute(
                    update(QuestionLog).where(QuestionLog.id == row_id).values(no_info=flag)
                )
                changed += 1


async def initialize(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Al arrancar: si nunca se han guardado las frases (instalación nueva o recién migrada),
    se guardan las de por defecto y se clasifica el histórico."""
    async with sessionmaker() as session:
        if await session.get(AppSetting, SETTINGS_KEY) is not None:
            return
        await DashboardSettingsRepository(session).save_phrases(DEFAULT_NO_INFO_PHRASES, "sistema")
        await session.commit()


# --- Registro ---


@dataclass(slots=True)
class QuestionRecord:
    """Lo que se registra de una pregunta al terminar (o fallar) la respuesta."""

    asked_at: datetime
    session_id: str | None
    user_id: str | None
    access_kind: str
    conversation_id: str | None
    question: str
    status: QuestionStatus = QuestionStatus.CANCELLED
    answer: str | None = None
    first_token_ms: int | None = None
    response_ms: int | None = None
    sources_retrieved: int = 0
    sources_cited: int = 0
    # Falló la búsqueda en la documentación (#95).
    retrieval_failed: bool = False
    # Auditoría (#50).
    client_ip: str | None = None
    user_agent: str | None = None
    user_display_name: str | None = None
    model: str | None = None
    prompt_version_id: int | None = None
    search_query: str | None = None
    cited_documents: list[dict[str, Any]] | None = None
    error_detail: str | None = None
    # Búsqueda limitada a unos documentos (#58) y su origen (`ScopeSource`).
    scope_document_ids: list[str] | None = None
    scope_source: str | None = None
    scope_category_ids: list[int] | None = None
    # Tamaño del prompt y recortes (#73).
    prompt_tokens_estimated: int | None = None
    prompt_tokens: int | None = None
    prompt_trim: dict[str, Any] | None = None


async def record_question(
    sessionmaker: async_sessionmaker[AsyncSession], record: QuestionRecord
) -> None:
    """Guarda la pregunta en el registro. Nunca falla hacia quien la llama: el chat no debe
    interrumpirse por un problema de estadísticas."""
    try:
        async with sessionmaker() as session:
            conversation_id = record.conversation_id
            if conversation_id and await session.get(Conversation, conversation_id) is None:
                conversation_id = None  # borrada mientras se respondía
            phrases = await DashboardSettingsRepository(session).phrases()
            session.add(
                QuestionLog(
                    asked_at=record.asked_at,
                    session_id=record.session_id,
                    user_id=record.user_id,
                    access_kind=record.access_kind,
                    conversation_id=conversation_id,
                    question=record.question,
                    answer=record.answer,
                    status=record.status.value,
                    no_info=says_no_info(record.answer, phrases),
                    first_token_ms=record.first_token_ms,
                    response_ms=record.response_ms,
                    sources_retrieved=record.sources_retrieved,
                    sources_cited=record.sources_cited,
                    retrieval_failed=record.retrieval_failed,
                    client_ip=record.client_ip,
                    user_agent=record.user_agent,
                    user_display_name=record.user_display_name,
                    model=record.model,
                    prompt_version_id=record.prompt_version_id,
                    search_query=record.search_query,
                    cited_documents=record.cited_documents,
                    error_detail=record.error_detail,
                    scope_document_ids=record.scope_document_ids,
                    scope_source=record.scope_source,
                    scope_category_ids=record.scope_category_ids,
                    prompt_tokens_estimated=record.prompt_tokens_estimated,
                    prompt_tokens=record.prompt_tokens,
                    prompt_trim=record.prompt_trim,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("No se pudo registrar la pregunta en el cuadro de mando")


# --- Estadísticas ---


@dataclass(frozen=True, slots=True)
class DateRange:
    """Rango de días (ambos incluidos) en la zona horaria de la aplicación."""

    start: date
    end: date
    zone: ZoneInfo

    @classmethod
    def parse(cls, start: date | None, end: date | None, zone: ZoneInfo) -> "DateRange":
        today = datetime.now(zone).date()
        end = end or today
        start = start or end - timedelta(days=29)
        if start > end:
            raise DashboardError("La fecha de inicio es posterior a la de fin")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise DashboardError(f"El rango admite como máximo {MAX_RANGE_DAYS} días")
        return cls(start, end, zone)

    @property
    def utc_bounds(self) -> tuple[datetime, datetime]:
        """Inicio (incluido) y fin (excluido) en UTC: tiene en cuenta el cambio de hora."""
        begin = datetime.combine(self.start, time.min, self.zone)
        finish = datetime.combine(self.end + timedelta(days=1), time.min, self.zone)
        return begin.astimezone(UTC), finish.astimezone(UTC)

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def local_day(self, moment: datetime) -> date:
        aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        return aware.astimezone(self.zone).date()


@dataclass(slots=True)
class DayStats:
    day: date
    sessions: int = 0
    users: int = 0
    questions: int = 0
    # Preguntas en las que no se pudo consultar la documentación (#99).
    retrieval_failed: int = 0


@dataclass(slots=True)
class Summary:
    sessions: int
    users: int
    questions: int
    answered: int  # respondidas sin decir que no tiene la información
    no_info: int
    errors: int  # error del modelo o cancelada
    without_sources: int  # respondidas sin documentación recuperada
    avg_response_ms: int | None
    median_response_ms: int | None
    avg_first_token_ms: int | None
    # Preguntas cuyo prompt se recortó para caber en la ventana del modelo (#73).
    trimmed: int = 0
    # Preguntas en las que falló la búsqueda en la documentación (embeddings o índice, #99),
    # respondidas sin ella o rechazadas (#97).
    retrieval_failed: int = 0
    days: list[DayStats] = field(default_factory=list)

    @property
    def answered_ratio(self) -> float | None:
        return self.answered / self.questions if self.questions else None


def trim_label(trim: dict[str, Any] | None) -> str:
    """Qué se quitó del prompt (#73), en palabras."""
    if not trim:
        return ""
    parts: list[str] = []
    if trim.get("full_document"):
        parts.append("documento completo sustituido por fragmentos")
    if sources := trim.get("sources"):
        parts.append(f"{sources} {'fragmento' if sources == 1 else 'fragmentos'}")
    if history := trim.get("history"):
        parts.append(f"{history} {'mensaje' if history == 1 else 'mensajes'} del historial")
    if trim.get("server"):
        parts.append("recortado por el servidor")
    if trim.get("underestimated"):
        parts.append("estimación por debajo del real")
    return "; ".join(parts)


def _in_range(rng: DateRange) -> Any:
    begin, finish = rng.utc_bounds
    return (QuestionLog.asked_at >= begin) & (QuestionLog.asked_at < finish)


async def summary(session: AsyncSession, rng: DateRange) -> Summary:
    rows = (
        await session.execute(
            select(
                QuestionLog.asked_at,
                QuestionLog.session_id,
                QuestionLog.user_id,
                QuestionLog.status,
                QuestionLog.no_info,
                QuestionLog.response_ms,
                QuestionLog.first_token_ms,
                QuestionLog.sources_retrieved,
                QuestionLog.prompt_trim,
                QuestionLog.retrieval_failed,
            ).where(_in_range(rng))
        )
    ).all()
    # Por día: sesiones y usuarios distintos, número de preguntas y fallos de la búsqueda.
    per_day: dict[date, tuple[set[str], set[str], list[None]]] = {
        day: (set(), set(), []) for day in rng.days()
    }
    failed_per_day: dict[date, int] = dict.fromkeys(rng.days(), 0)
    sessions: set[str] = set()
    users: set[str] = set()
    answered = no_info = errors = without_sources = trimmed = retrieval_failed = 0
    times: list[int] = []
    first_tokens: list[int] = []
    for row in rows:
        day = rng.local_day(row.asked_at)
        day_sessions, day_users, day_questions = per_day[day]
        day_questions.append(None)
        if row.retrieval_failed:
            retrieval_failed += 1
            failed_per_day[day] += 1
        if row.session_id:
            sessions.add(row.session_id)
            day_sessions.add(row.session_id)
        if row.user_id:
            users.add(row.user_id)
            day_users.add(row.user_id)
        if row.prompt_trim:
            trimmed += 1
        if row.status != QuestionStatus.OK.value:
            errors += 1
            continue
        if row.no_info:
            no_info += 1
        else:
            answered += 1
        if row.sources_retrieved == 0:
            without_sources += 1
        if row.response_ms is not None:
            times.append(row.response_ms)
        if row.first_token_ms is not None:
            first_tokens.append(row.first_token_ms)
    return Summary(
        sessions=len(sessions),
        users=len(users),
        questions=len(rows),
        answered=answered,
        no_info=no_info,
        errors=errors,
        without_sources=without_sources,
        trimmed=trimmed,
        retrieval_failed=retrieval_failed,
        avg_response_ms=round(statistics.fmean(times)) if times else None,
        median_response_ms=round(statistics.median(times)) if times else None,
        avg_first_token_ms=round(statistics.fmean(first_tokens)) if first_tokens else None,
        days=[
            DayStats(
                day=day,
                sessions=len(s),
                users=len(u),
                questions=len(q),
                retrieval_failed=failed_per_day[day],
            )
            for day, (s, u, q) in per_day.items()
        ],
    )


async def list_questions(
    session: AsyncSession, rng: DateRange, page: int, size: int
) -> tuple[int, list[QuestionLog]]:
    """Página de preguntas del rango, de la más reciente a la más antigua."""
    total = await session.scalar(
        select(func.count()).select_from(QuestionLog).where(_in_range(rng))
    )
    rows = await session.scalars(
        select(QuestionLog)
        .where(_in_range(rng))
        .order_by(QuestionLog.asked_at.desc(), QuestionLog.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return int(total or 0), list(rows)


async def all_questions(session: AsyncSession, rng: DateRange) -> list[QuestionLog]:
    rows = await session.scalars(
        select(QuestionLog)
        .where(_in_range(rng))
        .order_by(QuestionLog.asked_at.desc(), QuestionLog.id.desc())
    )
    return list(rows)


# --- Exportación ---

EXPORT_HEADERS = [
    "Fecha y hora",
    "Pregunta",
    "Respuesta",
    "Tiempo de respuesta (s)",
    "Primer fragmento (s)",
    "Resultado",
    "Fuentes citadas",
    "Fallo de la búsqueda",
]
STATUS_LABELS = {
    QuestionStatus.OK.value: "Respondida",
    QuestionStatus.ERROR.value: "Error del modelo",
    QuestionStatus.CANCELLED.value: "Cancelada",
}
# Un texto que empieza así lo interpretaría una hoja de cálculo como fórmula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(text: str | None) -> str:
    """Neutraliza la inyección de fórmulas (CSV/Excel) anteponiendo un apóstrofo."""
    if not text:
        return ""
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def result_label(row: QuestionLog) -> str:
    if row.status == QuestionStatus.OK.value and row.no_info:
        return "Sin información"
    return STATUS_LABELS.get(row.status, row.status)


def _yes_no(value: bool) -> str:
    return "Sí" if value else "No"


def _seconds(ms: int | None) -> float | None:
    return round(ms / 1000, 2) if ms is not None else None


def local_time(moment: datetime, zone: ZoneInfo) -> datetime:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(zone)


def to_csv(rows: Sequence[QuestionLog], zone: ZoneInfo) -> bytes:
    """CSV estándar (RFC 4180, UTF-8, coma; decimales con punto)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_HEADERS)
    for row in rows:
        writer.writerow(
            [
                local_time(row.asked_at, zone).strftime("%d/%m/%Y %H:%M:%S"),
                safe_cell(row.question),
                safe_cell(row.answer),
                _seconds(row.response_ms) if row.response_ms is not None else "",
                _seconds(row.first_token_ms) if row.first_token_ms is not None else "",
                result_label(row),
                row.sources_cited,
                _yes_no(row.retrieval_failed),
            ]
        )
    return buffer.getvalue().encode("utf-8")


def to_xlsx(rows: Sequence[QuestionLog], zone: ZoneInfo) -> bytes:
    """Libro de Excel con fechas y números de verdad (ordenables y filtrables)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Preguntas"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(
            [
                # Excel no admite zonas horarias: hora local sin zona.
                local_time(row.asked_at, zone).replace(tzinfo=None),
                safe_cell(row.question),
                safe_cell(row.answer),
                _seconds(row.response_ms),
                _seconds(row.first_token_ms),
                result_label(row),
                row.sources_cited,
                _yes_no(row.retrieval_failed),
            ]
        )
    for (cell,) in sheet.iter_rows(min_row=2, max_col=1):
        cell.number_format = "dd/mm/yyyy hh:mm:ss"
    for column, width in zip("ABCDEFGH", (19, 60, 80, 14, 14, 18, 10, 12), strict=True):
        sheet.column_dimensions[column].width = width
    for cells in sheet.iter_rows(min_row=2, min_col=2, max_col=3):
        for cell in cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
