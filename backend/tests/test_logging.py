import json
import logging

import pytest
from httpx import AsyncClient

from app.core.logging import (
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
    request_id_var,
)


def _record(message: str = "hola %s", **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {"name": "app.test", "levelno": logging.INFO, "levelname": "INFO", "msg": message}
    )
    record.args = ("mundo",)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_extra_fields_and_request_id() -> None:
    token = request_id_var.set("abc123")
    try:
        record = _record(conversation_id="c1")
        RequestIdFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["message"] == "hola mundo"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "abc123"
    assert payload["conversation_id"] == "c1"
    assert "time" in payload


def test_json_formatter_without_request_and_with_exception() -> None:
    try:
        raise ValueError("fallo")
    except ValueError:
        import sys

        record = _record(exc_info=sys.exc_info())
    RequestIdFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))

    assert "request_id" not in payload
    assert "ValueError: fallo" in payload["exception"]


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_configure_logging_replaces_only_its_own_handler(fmt: str) -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        configure_logging("debug", fmt)  # type: ignore[arg-type]
        configure_logging("INFO", fmt)  # type: ignore[arg-type]

        own = [h for h in root.handlers if h.get_name() == "chatbot"]
        assert len(own) == 1
        assert foreign in root.handlers
        assert isinstance(own[0].formatter, JsonFormatter) == (fmt == "json")
        assert root.level == logging.INFO
        assert logging.getLogger("uvicorn.access").handlers == []
    finally:
        root.removeHandler(foreign)


async def test_requests_get_an_id_and_are_logged(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="app.access"):
        generated = await client.get("/api/v1/conversations")
        reused = await client.get("/api/v1/conversations", headers={"X-Request-ID": "nginx-42"})
        invalid = await client.get("/api/v1/conversations", headers={"X-Request-ID": "mal id!"})
        await client.get("/api/v1/health")

    assert len(generated.headers["x-request-id"]) == 32
    assert reused.headers["x-request-id"] == "nginx-42"
    assert invalid.headers["x-request-id"] != "mal id!"

    access = [r for r in caplog.records if r.name == "app.access"]
    assert [(r.method, r.path, r.status) for r in access[:3]] == [  # type: ignore[attr-defined]
        ("GET", "/api/v1/conversations", 200)
    ] * 3
    assert access[0].levelno == logging.INFO
    # Los healthchecks se registran solo en DEBUG para no llenar el log.
    assert access[3].levelno == logging.DEBUG
    assert access[3].duration_ms >= 0  # type: ignore[attr-defined]


async def test_logs_during_a_request_carry_its_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = await client.post(
            "/api/v1/chat", json={"message": "hola"}, headers={"X-Request-ID": "req-1"}
        )

    assert response.status_code == 200
    access = next(r for r in caplog.records if r.name == "app.access")
    assert access.request_id == "req-1"  # type: ignore[attr-defined]
    assert request_id_var.get() is None
