"""Cuadro de mando (#51): registro de preguntas, estadísticas, API y exportación."""

import csv
import io
import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from pydantic import SecretStr
from sqlalchemy import select
from starlette.requests import Request

from app.api import admin
from app.core.auth import (
    SESSION_CHAT_ID_KEY,
    CurrentUser,
    access_kind,
    chat_session_id,
    end_session_user,
)
from app.core.config import Settings
from app.db.models import AccessKind, AppSetting, QuestionLog, QuestionStatus
from app.services import dashboard
from app.services.chat import ChatService
from app.services.dashboard import (
    DEFAULT_NO_INFO_PHRASES,
    DashboardError,
    DateRange,
    QuestionRecord,
    clean_phrases,
    record_question,
    safe_cell,
    says_no_info,
)
from tests.conftest import ScriptedLLM, parse_sse

MADRID = ZoneInfo("Europe/Madrid")
ADMIN = {"username": "admin", "password": "secreto"}
BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr(ADMIN["password"])
    return settings


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


@pytest.fixture
async def admin_client(app: FastAPI) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/admin/session", json=ADMIN)).status_code == 200
        yield client


async def log_rows(app: FastAPI) -> list[QuestionLog]:
    async with app.state.sessionmaker() as db:
        return list(await db.scalars(select(QuestionLog).order_by(QuestionLog.id)))


async def ask(client: AsyncClient, question: str) -> str:
    response = await client.post("/api/v1/chat", json={"message": question})
    assert response.status_code == 200, response.text
    return str(parse_sse(response.text)[0][1]["id"])


def local(day: date, hour: int = 12) -> datetime:
    """Momento en hora de Madrid, en UTC como se guarda."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=MADRID).astimezone(UTC)


async def insert(app: FastAPI, *rows: dict[str, Any]) -> None:
    async with app.state.sessionmaker() as db:
        for values in rows:
            db.add(
                QuestionLog(
                    **{
                        "session_id": "s1",
                        "user_id": "u1",
                        "access_kind": "ldap",
                        "question": "¿Pregunta?",
                        "answer": "Respuesta",
                        "status": "ok",
                        "no_info": False,
                        "response_ms": 1000,
                        "first_token_ms": 200,
                        "sources_retrieved": 1,
                        "sources_cited": 1,
                        **values,
                    }
                )
            )
        await db.commit()


# --- Reglas ---


def test_no_info_detection_ignores_case_and_accents() -> None:
    phrases = ["no dispongo de información"]
    assert says_no_info("Lo siento, NO DISPONGO DE INFORMACION sobre eso.", phrases)
    assert says_no_info("No   dispongo de\ninformación.", phrases)
    assert not says_no_info("Dispongo de información: son 23 días.", phrases)
    assert not says_no_info(None, phrases)
    assert not says_no_info("No dispongo de información", ["  "])


def test_clean_phrases() -> None:
    assert clean_phrases(["  No sé ", "no se", "", "Otra  frase"]) == ["No sé", "Otra frase"]
    with pytest.raises(DashboardError, match="200"):
        clean_phrases(["x" * 201])
    with pytest.raises(DashboardError, match="50"):
        clean_phrases([f"frase {i}" for i in range(51)])


def test_date_range_defaults_and_limits() -> None:
    today = datetime.now(MADRID).date()
    rng = DateRange.parse(None, None, MADRID)
    assert (rng.start, rng.end) == (today - timedelta(days=29), today)
    assert len(rng.days()) == 30
    with pytest.raises(DashboardError, match="posterior"):
        DateRange.parse(date(2026, 2, 2), date(2026, 2, 1), MADRID)
    with pytest.raises(DashboardError, match="366"):
        DateRange.parse(date(2025, 1, 1), date(2026, 1, 2), MADRID)


def test_days_follow_local_time_including_daylight_saving() -> None:
    # 29/03/2026: cambio de hora en España (el día dura 23 horas).
    rng = DateRange.parse(date(2026, 3, 29), date(2026, 3, 29), MADRID)
    assert rng.utc_bounds == (
        datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
    )
    # 00:30 hora de Madrid del 1 de enero es aún 31 de diciembre en UTC.
    assert rng.local_day(datetime(2025, 12, 31, 23, 30, tzinfo=UTC)) == date(2026, 1, 1)
    assert rng.local_day(datetime(2025, 12, 31, 23, 30)) == date(2026, 1, 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("¿Cuántos días?", "¿Cuántos días?"),
        (None, ""),
    ],
)
def test_exports_neutralize_formulas(text: str | None, expected: str) -> None:
    assert safe_cell(text) == expected


def test_session_id_and_access_kind() -> None:
    request = Request({"type": "http", "session": {}})
    first = chat_session_id(request)
    assert chat_session_id(request) == first
    end_session_user(request)
    assert SESSION_CHAT_ID_KEY not in request.session
    assert chat_session_id(request) != first

    person = CurrentUser(id="ana", display_name="Ana")
    anonymous = CurrentUser(id="anon:1", display_name="Anónimo", is_anonymous=True)
    assert access_kind("ldap", person) is AccessKind.LDAP
    assert access_kind("ldap", anonymous) is AccessKind.ANONYMOUS
    assert access_kind("none", anonymous) is AccessKind.PRIVATE
    assert access_kind("none", person) is AccessKind.SHARED


# --- Registro desde el chat ---


async def test_every_question_is_logged_with_times_and_session(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await ask(client, "Primera pregunta")
    await ask(client, "Segunda pregunta")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await ask(other, "Pregunta desde otro navegador")

    first, second, third = await log_rows(app)
    assert first.question == "Primera pregunta"
    assert first.answer == "Hola, mundo"
    assert first.status == QuestionStatus.OK.value
    assert first.access_kind == AccessKind.SHARED.value
    assert first.user_id == "anonymous"
    assert first.response_ms is not None
    assert first.first_token_ms is not None
    assert 0 <= first.first_token_ms <= first.response_ms
    assert first.session_id
    assert first.session_id == second.session_id
    assert third.session_id != first.session_id
    assert first.conversation_id is not None


async def test_model_errors_and_empty_answers_are_logged(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    llm.fail = True
    await ask(client, "Falla con texto parcial")
    llm.fail = False
    llm.chunks = ["   "]
    await ask(client, "Respuesta vacía")

    failed, empty = await log_rows(app)
    assert (failed.status, failed.answer) == ("error", "Hola, mundo")
    assert (empty.status, empty.answer) == ("error", None)
    assert failed.response_ms is not None


async def test_log_survives_deleting_the_conversation(app: FastAPI, client: AsyncClient) -> None:
    conversation = await ask(client, "Pregunta que se borrará")
    assert (await client.delete(f"/api/v1/conversations/{conversation}")).status_code == 204

    [row] = await log_rows(app)
    assert row.conversation_id is None
    assert (row.question, row.answer) == ("Pregunta que se borrará", "Hola, mundo")


async def test_cancelled_answers_are_logged(
    app: FastAPI, settings: Settings, llm: ScriptedLLM
) -> None:
    service = ChatService(app.state.sessionmaker, llm, settings, app.state.config.current)
    prepared = await service.prepare("anonymous", None, "Me voy antes de la respuesta")
    stream = cast(AsyncGenerator[str], service.stream(prepared))
    await anext(stream)  # evento «conversation»
    await stream.aclose()  # la persona cierra la conexión

    [row] = await log_rows(app)
    assert row.status == QuestionStatus.CANCELLED.value
    assert row.answer is None


async def test_answer_after_conversation_deleted_keeps_texts(app: FastAPI) -> None:
    record = QuestionRecord(
        asked_at=datetime.now(UTC),
        session_id="s",
        user_id="anonymous",
        access_kind="shared",
        conversation_id="no-existe",
        question="¿Algo?",
        status=QuestionStatus.OK,
        answer="Sí.",
    )
    await record_question(app.state.sessionmaker, record)
    [row] = await log_rows(app)
    assert (row.conversation_id, row.answer) == (None, "Sí.")


async def test_logging_problems_never_break_the_chat(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> Any:
        raise RuntimeError("BD caída")

    record = QuestionRecord(datetime.now(UTC), None, None, "shared", None, "¿Algo?")
    await record_question(broken, record)  # type: ignore[arg-type]
    assert "No se pudo registrar" in caplog.text


async def test_no_info_answers_are_flagged(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await dashboard.initialize(app.state.sessionmaker)
    llm.chunks = ["Los documentos no contienen información sobre el horario."]
    await ask(client, "¿Horario del gimnasio?")
    llm.chunks = ["Hay 23 días de vacaciones."]
    await ask(client, "¿Vacaciones?")

    no_info, answered = await log_rows(app)
    assert no_info.no_info is True
    assert answered.no_info is False


# --- Inicialización y frases ---


async def test_initialize_classifies_the_history_once(app: FastAPI) -> None:
    await insert(app, {"asked_at": local(date(2026, 1, 5)), "answer": "No tengo información."})

    await dashboard.initialize(app.state.sessionmaker)
    [row] = await log_rows(app)
    assert row.no_info is True
    async with app.state.sessionmaker() as db:
        setting = await db.get(AppSetting, dashboard.SETTINGS_KEY)
        assert setting is not None
        assert setting.value["no_info_phrases"] == DEFAULT_NO_INFO_PHRASES

    # Si ya están guardadas, no se tocan.
    async with app.state.sessionmaker() as db:
        await dashboard.DashboardSettingsRepository(db).save_phrases(["otra"], "admin")
        await db.commit()
    await dashboard.initialize(app.state.sessionmaker)
    async with app.state.sessionmaker() as db:
        assert await dashboard.DashboardSettingsRepository(db).phrases() == ["otra"]


async def test_changing_phrases_reclassifies_everything(
    app: FastAPI, admin_client: AsyncClient
) -> None:
    await insert(
        app,
        {"asked_at": local(date(2026, 1, 5)), "answer": "Lo desconozco por completo."},
        {"asked_at": local(date(2026, 1, 5)), "answer": "No tengo información."},
    )
    defaults = (await admin_client.get("/api/v1/admin/dashboard/no-info-phrases")).json()
    assert defaults["phrases"] == DEFAULT_NO_INFO_PHRASES

    response = await admin_client.put(
        "/api/v1/admin/dashboard/no-info-phrases", json={"phrases": ["lo desconozco"]}
    )
    assert response.status_code == 200
    assert response.json()["phrases"] == ["lo desconozco"]
    assert [r.no_info for r in await log_rows(app)] == [True, False]

    too_long = await admin_client.put(
        "/api/v1/admin/dashboard/no-info-phrases", json={"phrases": ["x" * 201]}
    )
    assert too_long.status_code == 422


# --- Estadísticas ---


async def test_summary_counts_sessions_users_questions_and_times(
    app: FastAPI, admin_client: AsyncClient
) -> None:
    day1, day2 = date(2026, 2, 10), date(2026, 2, 11)
    await insert(
        app,
        {"asked_at": local(day1, 9), "session_id": "a", "user_id": "ana", "response_ms": 1000},
        {"asked_at": local(day1, 10), "session_id": "a", "user_id": "ana", "response_ms": 3000},
        {"asked_at": local(day1, 11), "session_id": "b", "user_id": "luis", "no_info": True},
        {
            "asked_at": local(day2, 9),
            "session_id": "c",
            "user_id": "ana",
            "status": "error",
            "answer": None,
            "response_ms": 9000,
        },
        {
            "asked_at": local(day2, 23),
            "session_id": "c",
            "user_id": "ana",
            "sources_retrieved": 0,
            "retrieval_failed": True,
        },
        # Fuera del rango (día anterior, en hora de Madrid).
        {"asked_at": local(date(2026, 2, 9), 23), "session_id": "z", "user_id": "zoe"},
    )

    response = await admin_client.get(
        "/api/v1/admin/dashboard/summary", params={"from": "2026-02-10", "to": "2026-02-12"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["sessions"], body["users"], body["questions"]) == (3, 2, 5)
    assert (body["answered"], body["no_info"], body["errors"]) == (3, 1, 1)
    assert body["answered_ratio"] == pytest.approx(3 / 5)
    assert body["without_sources"] == 1
    assert body["retrieval_failed"] == 1
    # Media y mediana de las respondidas (los errores no cuentan): 1000, 3000, 1000, 1000.
    assert (body["avg_response_ms"], body["median_response_ms"]) == (1500, 1000)
    assert body["avg_first_token_ms"] == 200
    assert body["timezone"] == "Europe/Madrid"
    assert body["days"] == [
        {"day": "2026-02-10", "sessions": 2, "users": 2, "questions": 3, "retrieval_failed": 0},
        {"day": "2026-02-11", "sessions": 1, "users": 1, "questions": 2, "retrieval_failed": 1},
        {"day": "2026-02-12", "sessions": 0, "users": 0, "questions": 0, "retrieval_failed": 0},
    ]


async def test_empty_range(admin_client: AsyncClient) -> None:
    body = (await admin_client.get("/api/v1/admin/dashboard/summary")).json()
    assert body["questions"] == 0
    assert body["answered_ratio"] is None
    assert body["avg_response_ms"] is None
    assert len(body["days"]) == 30


async def test_invalid_ranges_are_rejected(admin_client: AsyncClient) -> None:
    url = "/api/v1/admin/dashboard/summary"
    reversed_range = await admin_client.get(url, params={"from": "2026-02-02", "to": "2026-02-01"})
    assert reversed_range.status_code == 422
    assert "posterior" in reversed_range.json()["detail"]
    assert (await admin_client.get(url, params={"from": "ayer"})).status_code == 422


async def test_questions_are_paginated_newest_first(
    app: FastAPI, admin_client: AsyncClient
) -> None:
    day = date(2026, 3, 2)
    await insert(
        app,
        *[
            {"asked_at": local(day, 8) + timedelta(minutes=i), "question": f"P{i}"}
            for i in range(12)
        ],
        {"asked_at": local(day, 20), "question": "Sin info", "no_info": True},
        {"asked_at": local(day, 21), "question": "Fallo", "status": "error", "answer": None},
        {"asked_at": local(day, 22), "question": "Cancelada", "status": "cancelled"},
    )
    url = "/api/v1/admin/dashboard/questions"
    params: dict[str, str | int] = {"from": "2026-03-02", "to": "2026-03-02", "size": 10}

    first = (await admin_client.get(url, params=params)).json()
    assert (first["total"], first["page"], first["size"]) == (15, 1, 10)
    assert [q["result"] for q in first["items"][:3]] == ["cancelled", "error", "no_info"]
    assert first["items"][3]["question"] == "P11"
    assert first["items"][3]["result"] == "answered"
    assert first["items"][3]["asked_at"].startswith("2026-03-02T08:11:00+01:00")

    second = (await admin_client.get(url, params={**params, "page": 2})).json()
    assert [q["question"] for q in second["items"]] == ["P4", "P3", "P2", "P1", "P0"]
    assert (await admin_client.get(url, params={**params, "size": 7})).status_code == 422


async def test_exports(app: FastAPI, admin_client: AsyncClient) -> None:
    await insert(
        app,
        {
            "asked_at": local(date(2026, 4, 1), 9),
            "question": "=1+1",
            "answer": "Son 23 días.",
            "response_ms": 1234,
        },
        {
            "asked_at": local(date(2026, 4, 1), 10),
            "status": "error",
            "answer": None,
            "retrieval_failed": True,
        },
        {"asked_at": local(date(2026, 4, 1), 11), "status": "ok", "no_info": True},
    )
    params = {"from": "2026-04-01", "to": "2026-04-01"}

    response = await admin_client.get("/api/v1/admin/dashboard/questions.csv", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        'filename="preguntas_2026-04-01_2026-04-01.csv"' in response.headers["content-disposition"]
    )
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    assert rows[0] == dashboard.EXPORT_HEADERS
    assert rows[1][0] == "01/04/2026 11:00:00"
    assert rows[1][5] == "Sin información"
    assert rows[2] == [
        "01/04/2026 10:00:00",
        "¿Pregunta?",
        "",
        "1.0",
        "0.2",
        "Error del modelo",
        "1",
        "Sí",
    ]
    assert rows[3] == [
        "01/04/2026 09:00:00",
        "'=1+1",
        "Son 23 días.",
        "1.23",
        "0.2",
        "Respondida",
        "1",
        "No",
    ]

    xlsx = await admin_client.get("/api/v1/admin/dashboard/questions.xlsx", params=params)
    assert xlsx.status_code == 200
    assert "spreadsheetml" in xlsx.headers["content-type"]
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    assert sheet is not None
    assert [c.value for c in sheet[1]] == dashboard.EXPORT_HEADERS
    asked, question, *_ = sheet[4]
    assert asked.value == datetime(2026, 4, 1, 9, 0)
    assert asked.number_format == "dd/mm/yyyy hh:mm:ss"
    # La fórmula se guarda como texto: no se ejecuta al abrir el fichero.
    assert (question.value, question.data_type) == ("'=1+1", "s")
    assert sheet["D4"].value == 1.23
    assert (sheet["H3"].value, sheet["H4"].value) == ("Sí", "No")


async def test_dashboard_is_only_for_admins(client: AsyncClient) -> None:
    for path in [
        "/api/v1/admin/dashboard/summary",
        "/api/v1/admin/dashboard/questions",
        "/api/v1/admin/dashboard/questions.csv",
        "/api/v1/admin/dashboard/questions.xlsx",
        "/api/v1/admin/dashboard/no-info-phrases",
    ]:
        assert (await client.get(path)).status_code == 401, path
    put = await client.put("/api/v1/admin/dashboard/no-info-phrases", json={"phrases": []})
    assert put.status_code == 401


# --- Migración ---


def test_migration_fills_the_log_from_existing_conversations(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(config, "b7d2e4f1a903")

    connection = sqlite3.connect(db)
    with connection:
        connection.executemany(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, 't', '2026-01-01 10:00:00', '2026-01-01 10:00:00')",
            [("c1", "ana"), ("c2", "anon:x"), ("c3", "anonymous")],
        )
        connection.executemany(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (1, "c1", "user", "¿Vacaciones?", "2026-01-01 10:00:00.000000"),
                (2, "c1", "assistant", "23 días [1].", "2026-01-01 10:00:02.500000"),
                (3, "c2", "user", "¿Sin respuesta?", "2026-01-02 09:00:00.000000"),
                (4, "c3", "user", "¿Horario?", "2026-01-03 09:00:00.000000"),
                (5, "c3", "assistant", "De 8 a 15.", "2026-01-03 09:00:01.000000"),
            ],
        )
        connection.execute(
            "INSERT INTO message_sources (message_id, number, filename, chunk_index, score, "
            "snippet, cited) VALUES (2, 1, 'a.md', 0, 0.9, 's', 1)"
        )
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(db)
    rows = connection.execute(
        "SELECT question, answer, status, response_ms, session_id, access_kind, "
        "sources_retrieved, sources_cited FROM question_log ORDER BY asked_at"
    ).fetchall()
    connection.close()
    assert rows == [
        ("¿Vacaciones?", "23 días [1].", "ok", 2500, "c1", "ldap", 1, 1),
        ("¿Sin respuesta?", None, "error", None, "c2", "anonymous", 0, 0),
        ("¿Horario?", "De 8 a 15.", "ok", 1000, "c3", "shared", 0, 0),
    ]
