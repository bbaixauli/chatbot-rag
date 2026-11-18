"""Configuración de los logs y trazabilidad de las peticiones.

Cada petición recibe un identificador (`X-Request-ID`, reutilizado si llega de nginx) que
se añade a todas las líneas de log emitidas mientras se atiende y se devuelve en la
respuesta. Nunca se registra el contenido de los mensajes del chat.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Peticiones frecuentes y poco interesantes (healthchecks): se registran en DEBUG.
_QUIET_PATHS = frozenset({"/api/v1/health"})

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

access_logger = logging.getLogger("app.access")

# Atributos estándar de LogRecord: el resto son campos extra (`logger.info(..., extra=...)`).
_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class RequestIdFilter(logging.Filter):
    """Añade `request_id` a cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Una línea JSON por registro, con los campos extra incluidos."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "-")
        if request_id != "-":
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_HANDLER_NAME = "chatbot"
TEXT_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"


def configure_logging(level: str, fmt: Literal["text", "json"]) -> None:
    """Configura el logger raíz (y los de uvicorn) con el formato indicado."""
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    handler.set_name(_HANDLER_NAME)
    root = logging.getLogger()
    # Solo se sustituye el handler propio (p. ej. al crear la app varias veces en los tests).
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn trae sus propios handlers: se unifican con el formato de la aplicación.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


class RequestContextMiddleware:
    """Asigna un id a cada petición y registra método, ruta, estado y duración.

    Es un middleware ASGI puro para no interferir con las respuestas en streaming (SSE).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope["path"]
            level = logging.DEBUG if path in _QUIET_PATHS and status_code < 400 else logging.INFO
            client = scope.get("client")
            access_logger.log(
                level,
                "%s %s %s %.1fms",
                scope["method"],
                path,
                status_code,
                duration_ms,
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                    "client": client[0] if client else None,
                    "request_id": request_id,
                },
            )
            request_id_var.reset(token)
