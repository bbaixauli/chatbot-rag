"""Cuadro de mando de la administración (#51): estadísticas, preguntas y exportación."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, SettingsDep
from app.core.auth import AdminDep
from app.core.config import Settings
from app.db.models import QuestionLog
from app.services import dashboard
from app.services.dashboard import DashboardError, DashboardSettingsRepository, DateRange

router = APIRouter(prefix="/admin/dashboard", tags=["dashboard"])

PAGE_SIZES = (10, 25, 50, 100)

FromDate = Annotated[date | None, Query(alias="from", description="Primer día (AAAA-MM-DD)")]
ToDate = Annotated[date | None, Query(alias="to", description="Último día, incluido")]


class DayOut(BaseModel):
    day: date
    sessions: int
    users: int
    questions: int


class SummaryOut(BaseModel):
    start: date
    end: date
    timezone: str
    sessions: int
    users: int
    questions: int
    answered: int
    answered_ratio: float | None
    no_info: int
    errors: int
    without_sources: int
    avg_response_ms: int | None
    median_response_ms: int | None
    avg_first_token_ms: int | None
    days: list[DayOut]


class QuestionOut(BaseModel):
    id: int
    asked_at: datetime
    question: str
    answer: str | None
    result: Literal["answered", "no_info", "error", "cancelled"]
    response_ms: int | None
    first_token_ms: int | None
    sources_cited: int


class QuestionPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[QuestionOut]


class PhrasesIn(BaseModel):
    phrases: list[str] = Field(max_length=200)


class PhrasesOut(BaseModel):
    phrases: list[str]
    defaults: list[str]


def _range(settings: Settings, start: date | None, end: date | None) -> DateRange:
    try:
        return DateRange.parse(start, end, settings.zone)
    except DashboardError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _result(row: QuestionLog) -> Literal["answered", "no_info", "error", "cancelled"]:
    if row.status == "ok":
        return "no_info" if row.no_info else "answered"
    return "cancelled" if row.status == "cancelled" else "error"


def _question_out(row: QuestionLog, settings: Settings) -> QuestionOut:
    return QuestionOut(
        id=row.id,
        asked_at=dashboard.local_time(row.asked_at, settings.zone),
        question=row.question,
        answer=row.answer,
        result=_result(row),
        response_ms=row.response_ms,
        first_token_ms=row.first_token_ms,
        sources_cited=row.sources_cited,
    )


@router.get("/summary", response_model=SummaryOut)
async def get_summary(
    _: AdminDep, db: DbSession, settings: SettingsDep, start: FromDate = None, end: ToDate = None
) -> SummaryOut:
    rng = _range(settings, start, end)
    stats = await dashboard.summary(db, rng)
    return SummaryOut(
        start=rng.start,
        end=rng.end,
        timezone=settings.timezone,
        sessions=stats.sessions,
        users=stats.users,
        questions=stats.questions,
        answered=stats.answered,
        answered_ratio=stats.answered_ratio,
        no_info=stats.no_info,
        errors=stats.errors,
        without_sources=stats.without_sources,
        avg_response_ms=stats.avg_response_ms,
        median_response_ms=stats.median_response_ms,
        avg_first_token_ms=stats.avg_first_token_ms,
        days=[
            DayOut(day=d.day, sessions=d.sessions, users=d.users, questions=d.questions)
            for d in stats.days
        ],
    )


@router.get("/questions", response_model=QuestionPage)
async def get_questions(
    _: AdminDep,
    db: DbSession,
    settings: SettingsDep,
    start: FromDate = None,
    end: ToDate = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query()] = 25,
) -> QuestionPage:
    """Preguntas y respuestas del rango, de la más reciente a la más antigua."""
    if size not in PAGE_SIZES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tamaño de página no admitido (usa {', '.join(map(str, PAGE_SIZES))})",
        )
    rng = _range(settings, start, end)
    total, rows = await dashboard.list_questions(db, rng, page, size)
    return QuestionPage(
        total=total, page=page, size=size, items=[_question_out(r, settings) for r in rows]
    )


def _download(content: bytes, media_type: str, rng: DateRange, extension: str) -> Response:
    filename = f"preguntas_{rng.start.isoformat()}_{rng.end.isoformat()}.{extension}"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/questions.csv", response_class=Response)
async def export_csv(
    _: AdminDep, db: DbSession, settings: SettingsDep, start: FromDate = None, end: ToDate = None
) -> Response:
    rng = _range(settings, start, end)
    rows = await dashboard.all_questions(db, rng)
    return _download(dashboard.to_csv(rows, settings.zone), "text/csv; charset=utf-8", rng, "csv")


@router.get("/questions.xlsx", response_class=Response)
async def export_xlsx(
    _: AdminDep, db: DbSession, settings: SettingsDep, start: FromDate = None, end: ToDate = None
) -> Response:
    rng = _range(settings, start, end)
    rows = await dashboard.all_questions(db, rng)
    return _download(
        dashboard.to_xlsx(rows, settings.zone),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        rng,
        "xlsx",
    )


@router.get("/no-info-phrases", response_model=PhrasesOut)
async def get_phrases(_: AdminDep, db: DbSession) -> PhrasesOut:
    phrases = await DashboardSettingsRepository(db).phrases()
    return PhrasesOut(phrases=phrases, defaults=dashboard.DEFAULT_NO_INFO_PHRASES)


@router.put("/no-info-phrases", response_model=PhrasesOut)
async def save_phrases(body: PhrasesIn, admin: AdminDep, db: DbSession) -> PhrasesOut:
    """Guarda las frases de «sin información» y vuelve a clasificar todo el registro."""
    try:
        phrases = await DashboardSettingsRepository(db).save_phrases(body.phrases, admin.username)
    except DashboardError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    return PhrasesOut(phrases=phrases, defaults=dashboard.DEFAULT_NO_INFO_PHRASES)
