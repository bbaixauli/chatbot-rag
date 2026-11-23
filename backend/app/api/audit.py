"""Auditoría (#50): avisos, ejecuciones del proceso de revisión y registro completo.

Quién consulta la auditoría queda en el log (trazabilidad, manual de estilos 6.1).
"""

import csv
import io
import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, SettingsDep
from app.core.auth import AdminDep, AdminUser
from app.core.config import Settings
from app.db.models import AuditAlert, AuditRun, AuditRunStatus, QuestionLog, utcnow
from app.services import dashboard
from app.services.audit import (
    AuditAlreadyRunningError,
    AuditDeps,
    run_in_background,
    send_test_email,
    start_run,
)
from app.services.audit_rules import CATEGORIES, SEVERITIES
from app.services.dashboard import DashboardError, DateRange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit", tags=["audit"])

PAGE_SIZES = (10, 25, 50, 100)
AlertStatus = Literal["new", "reviewed", "dismissed"]


def get_audit(request: Request) -> AuditDeps:
    deps: AuditDeps = request.app.state.audit
    return deps


AuditDepsDep = Annotated[AuditDeps, Depends(get_audit)]


def audit_access(request: Request, auditor: AdminDep) -> AdminUser:
    """Exige acceso a la auditoría y deja constancia en el log de quién consulta qué."""
    logger.info(
        "Acceso a la auditoría",
        extra={"user_id": auditor.username, "method": request.method, "path": request.url.path},
    )
    return auditor


AuditorDep = Annotated[AdminUser, Depends(audit_access)]


def _check_size(size: int) -> None:
    if size not in PAGE_SIZES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Tamaño de página no admitido (usa {', '.join(map(str, PAGE_SIZES))})",
        )


def _range(settings: Settings, start: date | None, end: date | None) -> DateRange:
    try:
        return DateRange.parse(start, end, settings.zone)
    except DashboardError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


# --- Modelos de salida ---


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    trigger: str
    triggered_by: str | None
    status: str
    reviewed: int
    reviewed_by_model: int
    model_errors: int
    alerts: int
    anonymized: int
    used_model: str | None
    error: str | None
    notification_error: str | None


class SummaryOut(BaseModel):
    new_alerts: int
    new_by_severity: dict[str, int]
    running: RunOut | None
    last_run: RunOut | None


class EntryOut(BaseModel):
    """Una pregunta del registro, con los datos de auditoría."""

    id: int
    asked_at: datetime
    user_id: str | None
    user_display_name: str | None
    access_kind: str
    client_ip: str | None
    user_agent: str | None
    question: str
    answer: str | None
    status: str
    response_ms: int | None
    model: str | None
    prompt_version_id: int | None
    search_query: str | None
    cited_documents: list[dict[str, Any]] | None
    error_detail: str | None


class AlertOut(BaseModel):
    id: int
    created_at: datetime
    run_id: int
    category: str
    severity: str
    source: str
    reason: str
    excerpt: str | None
    status: AlertStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    note: str | None
    entry: EntryOut


class AlertPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[AlertOut]


class AlertUpdate(BaseModel):
    status: AlertStatus
    note: str | None = Field(default=None, max_length=2000)


class EntryPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[EntryOut]


def _entry(row: QuestionLog, settings: Settings) -> EntryOut:
    return EntryOut(
        id=row.id,
        asked_at=dashboard.local_time(row.asked_at, settings.zone),
        user_id=row.user_id,
        user_display_name=row.user_display_name,
        access_kind=row.access_kind,
        client_ip=row.client_ip,
        user_agent=row.user_agent,
        question=row.question,
        answer=row.answer,
        status=row.status,
        response_ms=row.response_ms,
        model=row.model,
        prompt_version_id=row.prompt_version_id,
        search_query=row.search_query,
        cited_documents=row.cited_documents,
        error_detail=row.error_detail,
    )


def _alert(alert: AuditAlert, entry: QuestionLog, settings: Settings) -> AlertOut:
    return AlertOut(
        id=alert.id,
        created_at=dashboard.local_time(alert.created_at, settings.zone),
        run_id=alert.run_id,
        category=alert.category,
        severity=alert.severity,
        source=alert.source,
        reason=alert.reason,
        excerpt=alert.excerpt,
        status=alert.status,
        reviewed_by=alert.reviewed_by,
        reviewed_at=alert.reviewed_at,
        note=alert.note,
        entry=_entry(entry, settings),
    )


# --- Resumen y ejecuciones ---


@router.get("/summary", response_model=SummaryOut)
async def get_summary(_: AuditorDep, db: DbSession) -> SummaryOut:
    by_severity = dict(
        (
            await db.execute(
                select(AuditAlert.severity, func.count())
                .where(AuditAlert.status == "new")
                .group_by(AuditAlert.severity)
            )
        ).all()
    )
    running = await db.scalar(
        select(AuditRun).where(AuditRun.status == AuditRunStatus.RUNNING.value)
    )
    last = await db.scalar(
        select(AuditRun)
        .where(AuditRun.status != AuditRunStatus.RUNNING.value)
        .order_by(AuditRun.id.desc())
        .limit(1)
    )
    return SummaryOut(
        new_alerts=sum(by_severity.values()),
        new_by_severity={s: int(by_severity.get(s, 0)) for s in SEVERITIES},
        running=RunOut.model_validate(running) if running else None,
        last_run=RunOut.model_validate(last) if last else None,
    )


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    _: AuditorDep, db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[AuditRun]:
    return list(await db.scalars(select(AuditRun).order_by(AuditRun.id.desc()).limit(limit)))


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def run_now(auditor: AuditorDep, deps: AuditDepsDep) -> AuditRun:
    """Lanza la revisión en segundo plano; su estado se consulta en `/runs`."""
    try:
        run = await start_run(deps, "manual", auditor.username)
    except AuditAlreadyRunningError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya hay una revisión en curso. Espera a que termine."
        ) from exc
    run_in_background(deps, run.id)
    return run


@router.post("/test-email", status_code=status.HTTP_204_NO_CONTENT)
async def test_email(auditor: AuditorDep, deps: AuditDepsDep) -> Response:
    """Envía un correo de prueba con la configuración de avisos vigente."""
    audit = deps.config.current.audit
    if not audit.smtp_host or not audit.smtp_from or not audit.recipients:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Configura el servidor SMTP, el remitente y los destinatarios antes de probar.",
        )
    try:
        await send_test_email(deps, auditor.username)
    except Exception as exc:
        logger.warning("Falló el correo de prueba de la auditoría: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"No se pudo enviar el correo: {type(exc).__name__}"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Avisos ---


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    _: AuditorDep,
    db: DbSession,
    settings: SettingsDep,
    alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
    category: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query()] = 25,
) -> AlertPage:
    _check_size(size)
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Categoría no válida")
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Gravedad no válida")
    conditions = [
        condition
        for condition in (
            AuditAlert.status == alert_status if alert_status else None,
            AuditAlert.category == category if category else None,
            AuditAlert.severity == severity if severity else None,
        )
        if condition is not None
    ]
    total = await db.scalar(select(func.count()).select_from(AuditAlert).where(*conditions))
    rows = (
        await db.execute(
            select(AuditAlert, QuestionLog)
            .join(QuestionLog, QuestionLog.id == AuditAlert.question_log_id)
            .where(*conditions)
            .order_by(AuditAlert.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return AlertPage(
        total=int(total or 0),
        page=page,
        size=size,
        items=[_alert(alert, entry, settings) for alert, entry in rows],
    )


@router.patch("/alerts/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: int, body: AlertUpdate, auditor: AuditorDep, db: DbSession, settings: SettingsDep
) -> AlertOut:
    alert = await db.get(AuditAlert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado")
    alert.status = body.status
    alert.note = (body.note or "").strip() or None
    alert.reviewed_by = None if body.status == "new" else auditor.username
    alert.reviewed_at = None if body.status == "new" else utcnow()
    entry = await db.get(QuestionLog, alert.question_log_id)
    if entry is None:  # no ocurre: la clave foránea borra el aviso con la pregunta
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pregunta no encontrada")
    await db.commit()
    logger.info(
        "Aviso de auditoría actualizado",
        extra={"user_id": auditor.username, "alert_id": alert_id, "status": body.status},
    )
    return _alert(alert, entry, settings)


# --- Registro completo ---


FromDate = Annotated[date | None, Query(alias="from")]
ToDate = Annotated[date | None, Query(alias="to")]


def _log_conditions(rng: DateRange, user: str | None, ip: str | None) -> list[Any]:
    begin, finish = rng.utc_bounds
    conditions: list[Any] = [QuestionLog.asked_at >= begin, QuestionLog.asked_at < finish]
    if user:
        pattern = f"%{user.strip()}%"
        conditions.append(
            or_(QuestionLog.user_id.ilike(pattern), QuestionLog.user_display_name.ilike(pattern))
        )
    if ip:
        conditions.append(QuestionLog.client_ip.like(f"{ip.strip()}%"))
    return conditions


@router.get("/log", response_model=EntryPage)
async def list_log(
    _: AuditorDep,
    db: DbSession,
    settings: SettingsDep,
    start: FromDate = None,
    end: ToDate = None,
    user: Annotated[str | None, Query(max_length=255)] = None,
    ip: Annotated[str | None, Query(max_length=45)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query()] = 25,
) -> EntryPage:
    """Registro completo con usuario e IP (filtrable por fechas, usuario e IP)."""
    _check_size(size)
    conditions = _log_conditions(_range(settings, start, end), user, ip)
    total = await db.scalar(select(func.count()).select_from(QuestionLog).where(*conditions))
    rows = await db.scalars(
        select(QuestionLog)
        .where(*conditions)
        .order_by(QuestionLog.asked_at.desc(), QuestionLog.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return EntryPage(
        total=int(total or 0), page=page, size=size, items=[_entry(r, settings) for r in rows]
    )


LOG_HEADERS = [
    "Fecha y hora",
    "Usuario",
    "Nombre",
    "Acceso",
    "IP",
    "Navegador",
    "Pregunta",
    "Respuesta",
    "Resultado",
    "Tiempo de respuesta (s)",
    "Modelo",
    "Versión del prompt",
    "Consulta de búsqueda",
    "Documentos",
    "Error",
]


def _log_row(row: QuestionLog, settings: Settings, *, as_text: bool) -> list[Any]:
    asked = dashboard.local_time(row.asked_at, settings.zone)
    documents = ", ".join(str(d.get("filename")) for d in row.cited_documents or [])
    safe = dashboard.safe_cell
    return [
        asked.strftime("%d/%m/%Y %H:%M:%S") if as_text else asked.replace(tzinfo=None),
        safe(row.user_id),
        safe(row.user_display_name),
        row.access_kind,
        safe(row.client_ip),
        safe(row.user_agent),
        safe(row.question),
        safe(row.answer),
        dashboard.result_label(row),
        round(row.response_ms / 1000, 2) if row.response_ms is not None else "",
        safe(row.model),
        row.prompt_version_id or "",
        safe(row.search_query),
        safe(documents),
        safe(row.error_detail),
    ]


async def _log_rows(
    db: DbSession,
    settings: Settings,
    start: date | None,
    end: date | None,
    user: str | None,
    ip: str | None,
) -> tuple[DateRange, list[QuestionLog]]:
    rng = _range(settings, start, end)
    rows = await db.scalars(
        select(QuestionLog)
        .where(*_log_conditions(rng, user, ip))
        .order_by(QuestionLog.asked_at.desc(), QuestionLog.id.desc())
    )
    return rng, list(rows)


def _download(content: bytes, media_type: str, rng: DateRange, extension: str) -> Response:
    filename = f"auditoria_{rng.start.isoformat()}_{rng.end.isoformat()}.{extension}"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/log.csv", response_class=Response)
async def export_log_csv(
    _: AuditorDep,
    db: DbSession,
    settings: SettingsDep,
    start: FromDate = None,
    end: ToDate = None,
    user: Annotated[str | None, Query(max_length=255)] = None,
    ip: Annotated[str | None, Query(max_length=45)] = None,
) -> Response:
    rng, rows = await _log_rows(db, settings, start, end, user, ip)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(LOG_HEADERS)
    writer.writerows(_log_row(r, settings, as_text=True) for r in rows)
    return _download(buffer.getvalue().encode("utf-8"), "text/csv; charset=utf-8", rng, "csv")


@router.get("/log.xlsx", response_class=Response)
async def export_log_xlsx(
    _: AuditorDep,
    db: DbSession,
    settings: SettingsDep,
    start: FromDate = None,
    end: ToDate = None,
    user: Annotated[str | None, Query(max_length=255)] = None,
    ip: Annotated[str | None, Query(max_length=45)] = None,
) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    rng, rows = await _log_rows(db, settings, start, end, user, ip)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Auditoría"
    sheet.append(LOG_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(_log_row(row, settings, as_text=False))
    for (cell,) in sheet.iter_rows(min_row=2, max_col=1):
        cell.number_format = "dd/mm/yyyy hh:mm:ss"
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _download(
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        rng,
        "xlsx",
    )
