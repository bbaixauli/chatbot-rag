"""Actualización periódica de los documentos añadidos por su dirección web (#79)."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.db.models import Document, DocumentStatus, utcnow
from app.services.tasks import REFRESH_DOCUMENT
from app.services.web import WebFetcher
from app.services.web_refresh import schedule_due, scheduler_loop
from tests.conftest import set_config
from tests.web_server import Server

URL = "https://intranet/gastos"


def page(body: str, footer: str = "Aviso legal") -> str:
    return f"""<html><head><title>Política de gastos</title></head><body>
<nav>Inicio · Recursos humanos</nav>
<main><h1>Política de gastos</h1><p>{body}</p></main>
<footer>{footer}</footer></body></html>"""


OLD = "La dieta diaria nacional es de 53,34 euros para toda la plantilla de la empresa."
NEW = "La dieta diaria nacional sube a 60 euros para toda la plantilla de la empresa."


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
def server(app: FastAPI) -> Server:
    server = Server()
    app.state.documents.fetcher = WebFetcher(httpx.MockTransport(server.handle))
    return server


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


async def add(app: FastAPI, server: Server, url: str = URL, body: str = OLD, **headers: str) -> str:
    server.page(url, page(body), "text/html; charset=utf-8", **headers)
    result = await app.state.documents.add_url(url, "admin")
    await app.state.tasks.drain()
    document_id: str = result.document.id
    return document_id


async def document(app: FastAPI, document_id: str) -> Document:
    async with app.state.sessionmaker() as session:
        found: Document | None = await session.get(Document, document_id)
        assert found is not None
        return found


async def refresh(app: FastAPI, document_id: str) -> None:
    await app.state.documents.refresh(document_id)
    await app.state.tasks.drain()


async def stored_text(app: FastAPI, document_id: str) -> str:
    path = app.state.documents.file_path(await document(app, document_id))
    text: str = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return text


# --- Comprobación de un documento ---------------------------------------------------------


async def test_conditional_requests_and_not_modified(app: FastAPI, server: Server) -> None:
    document_id = await add(app, server, etag='"v1"', **{"last-modified": "Tue, 01 Dec 2026"})
    before = await document(app, document_id)

    def not_modified(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        assert request.headers["if-modified-since"] == "Tue, 01 Dec 2026"
        return httpx.Response(304, headers={"etag": '"v1"'})

    server.routes[URL] = not_modified
    await refresh(app, document_id)

    after = await document(app, document_id)
    assert after.source_checked_at is not None
    assert after.source_checked_at > before.source_checked_at  # type: ignore[operator]
    assert after.source_changed_at == before.source_changed_at
    assert (after.status, after.updated_at) == ("ready", before.updated_at)


async def test_changed_pages_are_replaced_and_reindexed(app: FastAPI, server: Server) -> None:
    document_id = await add(app, server)
    async with app.state.sessionmaker() as session:
        saved = await session.get(Document, document_id)
        assert saved is not None
        saved.title = "Gastos (título editado)"
        await session.commit()
    before = await document(app, document_id)

    server.page(URL, page(NEW), "text/html", etag='"v2"')
    await refresh(app, document_id)

    after = await document(app, document_id)
    assert (after.id, after.status, after.source_etag) == (document_id, "ready", '"v2"')
    assert after.sha256 != before.sha256
    assert after.source_changed_at is not None
    assert after.source_changed_at > before.source_changed_at  # type: ignore[operator]
    assert after.title == "Gastos (título editado)"  # el título editado se conserva
    assert "60 euros" in await stored_text(app, document_id)
    chunks = await app.state.vector_store.document_chunks(document_id, with_vectors=False)
    assert any("60 euros" in chunk.text for chunk in chunks)
    assert not any("53,34" in chunk.text for chunk in chunks)


async def test_changes_outside_the_main_content_are_ignored(app: FastAPI, server: Server) -> None:
    document_id = await add(app, server)
    before = await document(app, document_id)

    server.page(URL, page(OLD, footer="© 2027 Empresa · Nuevo aviso legal"), "text/html")
    await refresh(app, document_id)

    after = await document(app, document_id)
    assert (after.sha256, after.updated_at) == (before.sha256, before.updated_at)
    assert after.source_changed_at == before.source_changed_at


async def test_not_found_is_counted_until_the_document_is_deleted(
    app: FastAPI, server: Server
) -> None:
    await set_config(app, "web", not_found_limit=3)
    document_id = await add(app, server)
    del server.routes[URL]

    await refresh(app, document_id)
    await refresh(app, document_id)
    missing = await document(app, document_id)
    assert (missing.source_not_found, missing.source_error) == (2, "No encontrado (404)")

    # Vuelve a aparecer: el contador se reinicia.
    server.page(URL, page(OLD), "text/html")
    await refresh(app, document_id)
    assert (await document(app, document_id)).source_not_found == 0

    server.routes[URL] = lambda _: httpx.Response(410)
    for _ in range(3):
        await refresh(app, document_id)
    assert await app.state.documents.get(document_id) is None
    assert await app.state.vector_store.document_chunks(document_id) == []


async def test_other_errors_are_noted_but_not_counted(app: FastAPI, server: Server) -> None:
    await set_config(app, "web", not_found_limit=1)
    document_id = await add(app, server)

    server.routes[URL] = lambda _: httpx.Response(503)
    await refresh(app, document_id)
    failed = await document(app, document_id)
    assert (failed.source_error, failed.source_not_found) == ("El servidor respondió 503", 0)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    server.routes[URL] = refuse
    await refresh(app, document_id)
    assert (await document(app, document_id)).source_error == (
        "No se pudo conectar con el servidor: sin red"
    )

    # Una comprobación correcta borra el error.
    server.page(URL, page(OLD), "text/html")
    await refresh(app, document_id)
    assert (await document(app, document_id)).source_error is None


async def test_conflicts_keep_the_previous_version(app: FastAPI, server: Server) -> None:
    document_id = await add(app, server)
    await add(app, server, "https://intranet/otra", NEW)
    before = await document(app, document_id)

    # El contenido nuevo es el de otro documento (`sha256` es único).
    server.page(URL, page(NEW), "text/html")
    await refresh(app, document_id)
    same = await document(app, document_id)
    assert same.sha256 == before.sha256
    assert same.source_error == "El contenido nuevo es el mismo que el de «Política de gastos»"

    # Ahora es un PDF: el tipo no puede cambiar.
    server.routes[URL] = lambda _: httpx.Response(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )
    await refresh(app, document_id)
    assert (await document(app, document_id)).source_error == (
        "El tipo del documento ha cambiado (web → pdf)"
    )
    assert "53,34" in await stored_text(app, document_id)


async def test_documents_being_indexed_or_uploaded_are_skipped(
    app: FastAPI, server: Server
) -> None:
    document_id = await add(app, server)
    async with app.state.sessionmaker() as session:
        saved = await session.get(Document, document_id)
        assert saved is not None
        saved.status = DocumentStatus.PROCESSING.value
        await session.commit()
    requests = len(server.requests)

    await refresh(app, document_id)
    upload = await app.state.documents.upload("a.md", _Bytes(b"# Hola"), "admin")
    await app.state.documents.refresh(upload.document.id)
    await app.state.documents.refresh("no-existe")

    assert len(server.requests) == requests


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


# --- Programación -------------------------------------------------------------------------


class Recorder:
    """Ejecutor de tareas que solo anota lo que se encola."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, str, str | None]] = []

    def register(self, job: str, handler: Any) -> None:
        pass

    async def enqueue(self, job: str, argument: str, version: str | None = None) -> None:
        self.jobs.append((job, argument, version))

    async def drain(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def _set_checked(app: FastAPI, document_id: str, hours_ago: float | None) -> None:
    async with app.state.sessionmaker() as session:
        saved = await session.get(Document, document_id)
        assert saved is not None
        saved.source_checked_at = (
            None if hours_ago is None else utcnow() - timedelta(hours=hours_ago)
        )
        await session.commit()


async def test_due_documents_are_scheduled_once(app: FastAPI, server: Server) -> None:
    await set_config(app, "web", refresh_interval_hours=24)
    old = await add(app, server, "https://intranet/vieja", OLD)
    never = await add(app, server, "https://intranet/nunca", NEW)
    recent = await add(app, server, "https://intranet/reciente", "Texto reciente de la intranet.")
    await _set_checked(app, old, 25)
    await _set_checked(app, never, None)
    await _set_checked(app, recent, 1)
    recorder = Recorder()

    scheduled = await schedule_due(app.state.sessionmaker, app.state.config, recorder)
    # Una segunda vuelta (u otro proceso) no los vuelve a encolar: ya están reservados.
    again = await schedule_due(app.state.sessionmaker, app.state.config, recorder)

    assert (scheduled, again) == (2, 0)
    assert sorted(argument for _, argument, _ in recorder.jobs) == sorted([old, never])
    assert {job for job, _, _ in recorder.jobs} == {REFRESH_DOCUMENT}
    assert all(version for _, _, version in recorder.jobs)


async def test_scheduling_can_be_disabled(app: FastAPI, server: Server) -> None:
    document_id = await add(app, server)
    await _set_checked(app, document_id, None)
    await set_config(app, "web", refresh_enabled=False)
    recorder = Recorder()

    assert await schedule_due(app.state.sessionmaker, app.state.config, recorder) == 0
    assert recorder.jobs == []


async def test_scheduled_refreshes_run_through_the_task_runner(
    app: FastAPI, server: Server
) -> None:
    document_id = await add(app, server)
    await _set_checked(app, document_id, None)
    server.page(URL, page(NEW), "text/html")

    assert await schedule_due(app.state.sessionmaker, app.state.config, app.state.tasks) == 1
    await app.state.tasks.drain()
    await app.state.tasks.drain()  # la comprobación encola la indexación

    assert "60 euros" in await stored_text(app, document_id)
    assert (await document(app, document_id)).status == "ready"


async def test_the_scheduler_loop_survives_errors(app: FastAPI) -> None:
    calls = 0

    class Broken:
        @property
        def current(self) -> Any:
            raise RuntimeError("configuración rota")

    async def stop_after_two(_: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await scheduler_loop(app.state.sessionmaker, Broken(), Recorder(), sleep=stop_after_two)
    assert calls == 2


# --- API ----------------------------------------------------------------------------------


async def test_check_now_from_the_administration(
    app: FastAPI, admin: AsyncClient, server: Server
) -> None:
    document_id = await add(app, server)
    server.page(URL, page(NEW), "text/html")

    response = await admin.post(f"/api/v1/admin/documents/{document_id}/refresh")

    # Se comprueba en la petición: la respuesta ya trae el resultado (pendiente de reindexar).
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    await app.state.tasks.drain()
    [listed] = (await admin.get("/api/v1/admin/documents")).json()
    assert listed["source_url"] == URL
    assert listed["source_checked_at"] is not None
    assert listed["source_changed_at"] is not None
    assert (listed["source_error"], listed["source_not_found"]) == (None, 0)
    assert "60 euros" in await stored_text(app, document_id)

    upload = await app.state.documents.upload("a.md", _Bytes(b"# Hola"), "admin")
    not_web = await admin.post(f"/api/v1/admin/documents/{upload.document.id}/refresh")
    assert not_web.status_code == 422
    assert not_web.json()["detail"] == "El documento no se añadió por su dirección web"
    assert (await admin.post("/api/v1/admin/documents/nada/refresh")).status_code == 404


async def test_new_web_documents_are_not_due_right_away(app: FastAPI, server: Server) -> None:
    await add(app, server)
    recorder = Recorder()

    assert await schedule_due(app.state.sessionmaker, app.state.config, recorder) == 0


async def test_check_now_reports_documents_deleted_for_not_being_found(
    app: FastAPI, admin: AsyncClient, server: Server
) -> None:
    await set_config(app, "web", not_found_limit=1)
    document_id = await add(app, server)
    del server.routes[URL]

    response = await admin.post(f"/api/v1/admin/documents/{document_id}/refresh")

    assert response.status_code == 404
    assert (await admin.get("/api/v1/admin/documents")).json() == []
