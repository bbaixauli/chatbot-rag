"""Caracteres por token calibrados por modelo con el recuento del servidor (#102)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.runtime_config import LlmConfig, RuntimeConfig
from app.db.models import QuestionLog
from app.services.llm import ChatMessage
from app.services.prompt_budget import CHARS_PER_TOKEN, TokenEstimator
from app.services.token_estimates import (
    CALIBRATION_MAX,
    CALIBRATION_MIN,
    CALIBRATION_MIN_SAMPLES,
    TokenCalibration,
    calibrate,
    real_chars_per_token,
)
from tests.conftest import ScriptedLLM, set_config

NOW = datetime.now(UTC)
SINCE = NOW - timedelta(days=30)


def entry(model: str, estimated: int, counted: int | None, **values: Any) -> QuestionLog:
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


async def insert(app: FastAPI, *rows: QuestionLog) -> None:
    async with app.state.sessionmaker() as session:
        session.add_all(rows)
        await session.commit()


async def calibrated(app: FastAPI, model: str) -> Any:
    async with app.state.sessionmaker() as session:
        return await calibrate(session, model, SINCE)


def many(model: str, estimated: int, counted: int, n: int = CALIBRATION_MIN_SAMPLES) -> list[Any]:
    return [entry(model, estimated, counted) for _ in range(n)]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_estimator_uses_its_chars_per_token() -> None:
    message = ChatMessage("user", "x" * 70)
    assert TokenEstimator().message_tokens(message) == 20 + 8
    assert TokenEstimator(2.0).message_tokens(message) == 35 + 8
    assert TokenEstimator(2.0).tokens_to_chars(10) == 20
    kept, dropped = TokenEstimator(2.0).fit_recent([message, message], 50)
    assert (len(kept), dropped) == (1, 1)


def test_real_chars_per_token() -> None:
    # Sin el valor guardado, la pregunta se estimó con 3,5.
    assert real_chars_per_token(1000, 1250, None) == pytest.approx(2.8)
    assert real_chars_per_token(1000, 1000, 2.8) == pytest.approx(2.8)


async def test_calibration_needs_enough_samples(app: FastAPI) -> None:
    await insert(app, *many("m", 1000, 1250, CALIBRATION_MIN_SAMPLES - 1))

    result = await calibrated(app, "m")

    assert (result.chars_per_token, result.calibrated) == (CHARS_PER_TOKEN, False)
    assert result.samples == CALIBRATION_MIN_SAMPLES - 1


async def test_calibration_covers_most_prompts_and_ignores_other_rows(app: FastAPI) -> None:
    await insert(
        app,
        # El 95 % de los prompts: 2,8 caracteres por token reales (con 3,5 se quedaba corta).
        *many("m", 1000, 1250, 38),
        # Dos con más caracteres por token: no bajan la calibración.
        *many("m", 1000, 700, 2),
        # No cuentan: otro modelo, sin recuento, recortadas por el servidor o antiguas.
        *many("otro", 1000, 2000),
        entry("m", 1000, None),
        entry("m", 1000, 3000, prompt_trim={"server": True}),
        entry("m", 1000, 3000, asked_at=NOW - timedelta(days=40)),
    )

    result = await calibrated(app, "m")

    assert result.calibrated
    assert result.samples == 40
    assert result.chars_per_token == pytest.approx(2.8)


async def test_calibration_uses_the_stored_chars_per_token(app: FastAPI) -> None:
    # Estimadas ya con 2,8: lo contado coincide con lo estimado.
    await insert(app, *(entry("m", 1000, 1000, prompt_chars_per_token=2.8) for _ in range(30)))

    assert (await calibrated(app, "m")).chars_per_token == pytest.approx(2.8)


@pytest.mark.parametrize(("counted", "expected"), [(2000, CALIBRATION_MIN), (500, CALIBRATION_MAX)])
async def test_calibration_is_bounded(app: FastAPI, counted: int, expected: float) -> None:
    await insert(app, *many("m", 1000, counted))

    assert (await calibrated(app, "m")).chars_per_token == expected


async def test_fixed_value_wins_and_calibration_is_cached(app: FastAPI) -> None:
    llm = app.state.config.current.llm
    clock = Clock()
    calibration = TokenCalibration(app.state.sessionmaker, ttl=3600, clock=clock)
    # Fijado en la configuración: no se calibra.
    fixed = await calibration.estimator(llm.model_copy(update={"chars_per_token": 4.2}))
    assert fixed.chars_per_token == 4.2

    assert (await calibration.estimator(llm)).chars_per_token == CHARS_PER_TOKEN
    await insert(app, *many(llm.model, 1000, 1250))
    # Hasta que caduca, se reutiliza la calibración anterior.
    clock.now += 3599
    assert (await calibration.estimator(llm)).chars_per_token == CHARS_PER_TOKEN
    clock.now += 1
    assert (await calibration.estimator(llm)).chars_per_token == pytest.approx(2.8)
    # Otro modelo empieza sin calibrar.
    other = llm.model_copy(update={"model": "nuevo"})
    assert (await calibration.estimator(other)).chars_per_token == CHARS_PER_TOKEN


async def test_calibration_errors_fall_back_to_the_default() -> None:
    def broken() -> Any:
        raise RuntimeError("sin base de datos")

    calibration = TokenCalibration(broken)  # type: ignore[arg-type]

    result = await calibration.calibration("m")

    assert (result.chars_per_token, result.calibrated) == (CHARS_PER_TOKEN, False)


def test_chars_per_token_configuration() -> None:
    llm = RuntimeConfig.from_settings(Settings()).llm
    assert llm.chars_per_token == 0  # automático por defecto
    assert LlmConfig.model_validate({**llm.model_dump(), "chars_per_token": 2.5}).chars_per_token
    for invalid in (0.5, 9, -1):
        with pytest.raises(ValidationError):
            LlmConfig.model_validate({**llm.model_dump(), "chars_per_token": invalid})


async def last_log(app: FastAPI) -> QuestionLog:
    async with app.state.sessionmaker() as session:
        row: QuestionLog | None = await session.scalar(
            select(QuestionLog).order_by(QuestionLog.id.desc())
        )
    assert row is not None
    return row


async def test_chat_estimates_with_the_calibrated_value(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    model = app.state.config.current.llm.model
    question = "¿Cuántos días de vacaciones tengo al año?"

    await client.post("/api/v1/chat", json={"message": question})
    default = await last_log(app)
    assert default.prompt_chars_per_token == CHARS_PER_TOKEN

    await insert(app, *many(model, 1000, 1250))
    app.state.token_calibration = TokenCalibration(app.state.sessionmaker)
    await client.post("/api/v1/chat", json={"message": question})
    calibrated_row = await last_log(app)
    assert calibrated_row.prompt_chars_per_token == pytest.approx(2.8)
    # Con menos caracteres por token, el mismo prompt se estima más grande.
    assert calibrated_row.prompt_tokens_estimated is not None
    assert default.prompt_tokens_estimated is not None
    assert calibrated_row.prompt_tokens_estimated > default.prompt_tokens_estimated

    # Un valor fijo en la configuración manda sobre la calibración.
    await set_config(app, "llm", chars_per_token=4.0)
    await client.post("/api/v1/chat", json={"message": question})
    assert (await last_log(app)).prompt_chars_per_token == 4.0
