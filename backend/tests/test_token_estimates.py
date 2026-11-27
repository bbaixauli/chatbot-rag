"""Precisión de la estimación de tokens por modelo (#100)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.db.base import Base
from app.db.models import QuestionLog
from app.diagnose import main
from app.main import create_app
from app.services.prompt_budget import CHARS_PER_TOKEN
from app.services.token_estimates import model_estimates, quantile

NOW = datetime.now(UTC)


def row(
    model: str | None, estimated: int | None, counted: int | None, **values: Any
) -> QuestionLog:
    return QuestionLog(
        **{
            "asked_at": NOW - timedelta(days=1),
            "access_kind": "shared",
            "question": "¿Pregunta?",
            "status": "ok",
            "model": model,
            "prompt_tokens_estimated": estimated,
            "prompt_tokens": counted,
            **values,
        }
    )


async def _insert(settings: Settings, *rows: QuestionLog) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with application.state.sessionmaker() as session:
            session.add_all(rows)
            await session.commit()


async def _estimates(settings: Settings, since: datetime, limit: int | None = None) -> Any:
    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        application.state.sessionmaker() as session,
    ):
        return await model_estimates(session, since, limit)


def test_quantile_interpolates() -> None:
    assert quantile([1.0], 0.95) == 1.0
    assert quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert quantile([0.0, 10.0], 0.95) == pytest.approx(9.5)
    with pytest.raises(ValueError, match="Sin valores"):
        quantile([], 0.5)


async def test_estimates_by_model(settings: Settings) -> None:
    await _insert(
        settings,
        # Modelo con la estimación holgada: el real es el 80 % de lo estimado.
        *(row("holgado", 1000, 800) for _ in range(19)),
        row("holgado", 1000, 900),
        # Una pregunta sin recuento del servidor y otra en la que el chat detectó un recorte.
        row("holgado", 1000, None),
        row("holgado", 1000, 300, prompt_trim={"server": True}),
        # Modelo en el que la estimación se queda corta: el real es el 120 % de lo estimado.
        *(row("corto", 5000, 6000) for _ in range(5)),
        # Sin estimación (p. ej. el prompt no cabía) o fuera del periodo: no cuentan.
        row("corto", None, 4000),
        row("corto", 1000, 2000, asked_at=NOW - timedelta(days=40)),
        # Servidor que nunca devuelve el recuento.
        row(None, 1000, None),
    )

    holgado, corto, unknown = await _estimates(settings, NOW - timedelta(days=30), limit=5500)

    assert holgado.model == "holgado"
    assert (holgado.samples, holgado.without_count) == (21, 1)
    assert holgado.median == pytest.approx(0.8)
    assert holgado.p95 is not None
    assert holgado.p95 < 1
    assert holgado.max == pytest.approx(0.9)
    assert holgado.underestimated == 0
    assert holgado.server_trimmed == 1
    assert holgado.safe is True
    assert holgado.safe_chars_per_token is not None
    assert holgado.safe_chars_per_token > CHARS_PER_TOKEN

    assert corto.model == "corto"
    assert corto.samples == 5
    assert corto.p95 == pytest.approx(1.2)
    assert corto.underestimated == 5
    assert corto.over_limit == 5
    assert corto.safe is False
    assert corto.safe_chars_per_token == pytest.approx(CHARS_PER_TOKEN / 1.2)

    assert unknown.model == "(sin modelo)"
    assert (unknown.samples, unknown.without_count) == (0, 1)
    assert unknown.median is None
    assert unknown.safe is None


def test_cli(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(_insert(settings, *(row("holgado", 1000, 800) for _ in range(3))))

    assert main(["tokens"], settings) == 0
    output = capsys.readouterr().out
    assert "últimos 30 días" in output
    assert "holgado" in output
    assert "3 preguntas con recuento del servidor, 0 sin recuento" in output
    assert "mediana 0,80 · p95 0,80 · máx. 0,80" in output
    assert "La estimación es segura" in output
    # Calibración automática (#102): con 3 preguntas aún no hay suficientes.
    assert "Caracteres por token: automáticos" in output
    assert "aún no hay preguntas suficientes (3 de 30)" in output
    # Sin ventana de contexto configurada no se compara con el límite.
    assert "límite actual" not in output

    asyncio.run(_insert(settings, row("corto", 1000, 1500), row(None, 1000, None)))
    assert main(["tokens", "--days", "7"], settings) == 2
    output = capsys.readouterr().out
    assert "últimos 7 días" in output
    assert "se queda corta" in output
    assert "harían falta 2,33 caracteres por token" in output
    assert "no ha devuelto el recuento" in output


def test_cli_without_questions(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(_insert(settings))

    assert main(["tokens"], settings) == 0
    assert "No hay preguntas" in capsys.readouterr().out


def test_cli_with_context_window(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    # La ventana se copia a la configuración en el primer arranque: límite 3000 - 1024.
    settings.llm_context_window_tokens = 3000
    asyncio.run(
        _insert(
            settings,
            row("modelo", 2000, 1800),
            row("modelo", 2500, 2200),
            row("modelo", 3000, 900, prompt_trim={"server": True}),
        )
    )

    assert main(["tokens"], settings) == 0
    output = capsys.readouterr().out
    assert "Real por encima del límite actual (1976 tokens): 1 (33,33 %)" in output
    assert "Recortes detectados por el servidor: 1" in output


def test_cli_shows_the_calibration(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(_insert(settings, *(row("modelo", 1000, 1250) for _ in range(30))))

    main(["tokens"], settings)

    output = capsys.readouterr().out
    assert "Calibración automática: 2,80 caracteres por token (30 preguntas)" in output


def test_cli_with_a_fixed_value(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    # Valor inicial de la configuración (se copia a la BD en el primer arranque).
    settings.llm_chars_per_token = 3.0
    asyncio.run(_insert(settings, row("modelo", 1000, 1250)))

    main(["tokens"], settings)

    output = capsys.readouterr().out
    assert "Caracteres por token: 3,00 (fijados en la configuración)" in output
    assert "Calibración automática" not in output
