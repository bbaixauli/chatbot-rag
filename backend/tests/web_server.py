"""Servidor web simulado para los tests de documentos web (#78, #79)."""

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

Handler = Callable[[httpx.Request], Any]


class Server:
    """Servidor web simulado: respuestas por dirección y registro de peticiones."""

    def __init__(self) -> None:
        self.routes: dict[str, Handler] = {}
        self.requests: list[httpx.Request] = []

    def page(self, url: str, body: bytes | str, content_type: str, **headers: str) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.routes[url] = lambda _: httpx.Response(
            200, content=data, headers={"content-type": content_type, **headers}
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get(str(request.url))
        if handler is None:
            return httpx.Response(404)
        response = handler(request)
        if asyncio.iscoroutine(response):
            response = await response
        assert isinstance(response, httpx.Response)
        return response
