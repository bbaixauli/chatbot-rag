"""Documentos añadidos por su dirección web (#78)."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.runtime_config import WebConfig
from app.db.models import Document
from app.services.web import WebError, WebFetcher, check_url
from tests.conftest import ScriptedLLM, parse_sse, set_config
from tests.factories import pdf_bytes
from tests.office_factories import make_docx
from tests.web_server import Handler, Server

URLS = "/api/v1/admin/documents/urls"

PAGE = """<html><head><title>Política de gastos | Intranet</title></head><body>
<header><nav><a href="/">Inicio</a> <a href="/rrhh">Recursos humanos</a></nav></header>
<div class="cookies">Usamos cookies para mejorar tu experiencia. <button>Aceptar</button></div>
<main><article>
<h1>Política de gastos</h1>
<p>Esta política regula los gastos de viaje de toda la plantilla de la empresa.</p>
<h2>Dietas</h2>
<p>La dieta diaria nacional es de 53,34 euros y la internacional depende del país.</p>
<h2>Kilometraje</h2>
<p>Se abonan 0,26 euros por kilómetro recorrido con vehículo propio.</p>
</article></main>
<footer>© 2026 Empresa · Aviso legal · Mapa del sitio</footer>
</body></html>"""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    settings.chunk_size_chars = 300
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


async def add(client: AsyncClient, *urls: str, **options: Any) -> list[dict[str, Any]]:
    response = await client.post(URLS, json={"urls": list(urls), **options})
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()
    return results


def errors(results: list[dict[str, Any]]) -> list[str | None]:
    return [r["error"] for r in results]


# --- Comprobación de la dirección ---------------------------------------------------------


def _web(domains: str | None = None) -> WebConfig:
    return WebConfig(
        timeout_seconds=5,
        allowed_domains=domains,
        refresh_enabled=True,
        refresh_interval_hours=24,
        not_found_limit=3,
    )


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://intranet/a.pdf", "Solo se admiten direcciones http:// o https://"),
        ("file:///etc/passwd", "Solo se admiten direcciones http:// o https://"),
        ("intranet/a.pdf", "Solo se admiten direcciones http:// o https://"),
        ("http://", "La dirección no es válida"),
        ("https://ana:secreto@intranet/a.pdf", "No se admiten direcciones con usuario"),
        ("https://intranet/" + "a" * 2100, "demasiado larga"),
    ],
)
def test_invalid_urls_are_rejected(url: str, message: str) -> None:
    with pytest.raises(WebError, match=message):
        check_url(url, _web())


def test_allowed_domains_include_their_subdomains() -> None:
    web = _web(" empresa.es, .intranet.local ,")
    assert web.domains == ["empresa.es", "intranet.local"]
    for url in ("https://empresa.es/a", "https://www.empresa.es/a", "http://wiki.intranet.local"):
        assert check_url(f"  {url} ", web) == url
    for url in ("https://otraempresa.es/a", "https://empresa.es.malo.com/a"):
        with pytest.raises(WebError, match="no está entre los permitidos"):
            check_url(url, web)


# --- Alta por dirección -------------------------------------------------------------------


async def test_web_pages_are_cleaned_and_indexed(
    app: FastAPI, admin: AsyncClient, server: Server, llm: ScriptedLLM
) -> None:
    url = "https://intranet.empresa.es/rrhh/politica-gastos"
    server.page(url, PAGE, "text/html; charset=utf-8", etag='"v1"')

    [result] = await add(admin, url)
    await app.state.tasks.drain()

    assert result["error"] is None
    document = result["document"]
    assert (document["filename"], document["title"], document["kind"]) == (
        "politica-gastos.html",
        "Política de gastos",  # sin « | Intranet»
        "web",
    )
    assert document["source_url"] == url
    stored = app.state.documents.file_path(await app.state.documents.get(document["id"]))
    content = await asyncio.to_thread(stored.read_text, encoding="utf-8")
    assert stored.suffix == ".md"
    assert "## Dietas" in content
    assert "53,34 euros" in content
    for noise in ("cookies", "Aviso legal", "Inicio"):
        assert noise not in content
    async with app.state.sessionmaker() as session:
        saved = await session.get(Document, document["id"])
        assert saved is not None
        assert (saved.status, saved.source_etag) == ("ready", '"v1"')

    # La referencia enlaza a la página original.
    llm.chunks = ["La dieta es de 53,34 euros [1]."]
    chat = await admin.post(
        "/api/v1/chat",
        json={"message": "¿Cuánto es la dieta nacional?", "document_ids": [document["id"]]},
    )
    [source] = next(data["sources"] for kind, data in parse_sse(chat.text) if kind == "sources")
    assert (source["title"], source["url"]) == ("Política de gastos", url)
    conversation = parse_sse(chat.text)[0][1]["id"]
    messages = (await admin.get(f"/api/v1/conversations/{conversation}/messages")).json()
    assert messages[-1]["sources"][0]["url"] == url

    # La descarga desde la administración es el contenido limpio.
    download = await admin.get(f"/api/v1/admin/documents/{document['id']}/file")
    assert 'filename="politica-gastos.md"' in download.headers["content-disposition"]
    assert "## Dietas" in download.text


async def test_files_are_kept_as_they_are(
    app: FastAPI, admin: AsyncClient, server: Server, tmp_path: Path
) -> None:
    pdf = pdf_bytes(tmp_path, ["Manual del empleado"], title="Manual del empleado")
    docx = make_docx(tmp_path / "normas.docx").read_bytes()
    server.page("https://intranet/docs/manual.pdf", pdf, "application/pdf")
    # Sin un tipo útil: se deduce de la extensión de la dirección.
    server.page("https://intranet/docs/normas.docx?v=2", docx, "application/octet-stream")
    server.page("https://intranet/descargar?id=7", pdf_bytes(tmp_path, ["Otro"]), "application/pdf")

    results = await add(
        admin,
        "https://intranet/docs/manual.pdf",
        "https://intranet/docs/normas.docx?v=2",
        "https://intranet/descargar?id=7",
    )
    await app.state.tasks.drain()

    assert errors(results) == [None, None, None]
    assert [(r["document"]["filename"], r["document"]["kind"]) for r in results] == [
        ("manual.pdf", "pdf"),
        ("normas.docx", "docx"),
        ("descargar.pdf", "pdf"),
    ]
    assert results[0]["document"]["title"] == "Manual del empleado"
    documents = (await admin.get("/api/v1/admin/documents")).json()
    assert {d["status"] for d in documents} == {"ready"}


async def test_category_and_filename_title_are_applied(admin: AsyncClient, server: Server) -> None:
    category = (await admin.post("/api/v1/admin/categories", json={"name": "Web"})).json()["id"]
    server.page("https://intranet/gastos", PAGE, "text/html")

    [result] = await add(admin, "https://intranet/gastos", category_id=category, use_filename=True)

    assert (result["document"]["title"], result["document"]["category_id"]) == (
        "gastos.html",
        category,
    )


async def test_pages_in_other_encodings(admin: AsyncClient, server: Server) -> None:
    html = "<html><head><title>Guía de vacaciones</title></head><body><main>"
    html += "<h1>Guía de vacaciones</h1>"
    html += "<p>Los empleados tienen veintitrés días de vacaciones al año, más los festivos.</p>"
    html += "</main></body></html>"
    server.page("https://intranet/latin1", html.encode("latin-1"), "text/html; charset=ISO-8859-1")
    server.page("https://intranet/cp1252", html.encode("cp1252"), "text/html")

    results = await add(admin, "https://intranet/latin1", "https://intranet/cp1252")

    assert [r["document"]["title"] for r in results] == ["Guía de vacaciones"] * 2


async def test_already_added_urls_and_contents_are_duplicates(
    admin: AsyncClient, server: Server
) -> None:
    server.page("https://intranet/a", PAGE, "text/html")
    server.page("https://intranet/copia", PAGE, "text/html")
    [first] = await add(admin, "https://intranet/a")
    requests = len(server.requests)

    again, copy = await add(admin, "https://intranet/a", "https://intranet/copia")

    assert (again["duplicate"], again["document"]["id"]) == (True, first["document"]["id"])
    # La misma dirección no se vuelve a descargar.
    assert [str(r.url) for r in server.requests[requests:]] == ["https://intranet/copia"]
    # El mismo contenido desde otra dirección: el documento que ya existía.
    assert (copy["duplicate"], copy["document"]["id"]) == (True, first["document"]["id"])


async def test_reindexing_does_not_download_again(
    app: FastAPI, admin: AsyncClient, server: Server
) -> None:
    server.page("https://intranet/a", PAGE, "text/html")
    await add(admin, "https://intranet/a")
    await app.state.tasks.drain()
    requests = len(server.requests)

    await admin.post("/api/v1/admin/documents/reindex")
    await app.state.tasks.drain()

    assert len(server.requests) == requests
    [document] = (await admin.get("/api/v1/admin/documents")).json()
    assert document["status"] == "ready"
    assert document["chunk_count"] > 0


# --- Errores ------------------------------------------------------------------------------


async def test_download_errors_are_explained(admin: AsyncClient, server: Server) -> None:
    server.routes["https://intranet/caido"] = lambda _: httpx.Response(503)
    server.routes["https://intranet/foto"] = lambda _: httpx.Response(
        200, content=b"\x89PNG", headers={"content-type": "image/png"}
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexión rechazada", request=request)

    server.routes["https://apagado/"] = refuse

    results = await add(
        admin,
        "https://intranet/no-existe",
        "https://intranet/caido",
        "https://intranet/foto",
        "https://apagado/",
        "ftp://intranet/a.pdf",
    )

    assert errors(results) == [
        "No encontrado (404)",
        "El servidor respondió 503",
        "Tipo de documento no admitido (image/png): se aceptan páginas web, PDF, Markdown, "
        "Word, PowerPoint, Excel y OpenDocument",
        "No se pudo conectar con el servidor: conexión rechazada",
        "Solo se admiten direcciones http:// o https://",
    ]
    assert (await admin.get("/api/v1/admin/documents")).json() == []


async def test_redirects_are_followed_and_checked(
    app: FastAPI, admin: AsyncClient, server: Server
) -> None:
    await set_config(app, "web", allowed_domains="empresa.es")

    def redirect(location: str) -> Handler:
        return lambda _: httpx.Response(302, headers={"location": location})

    server.routes["https://www.empresa.es/viejo"] = redirect("/nuevo")
    server.page("https://www.empresa.es/nuevo", PAGE, "text/html")
    server.routes["https://www.empresa.es/fuera"] = redirect("https://otra.com/a")
    server.routes["https://www.empresa.es/ftp"] = redirect("ftp://www.empresa.es/a")
    server.routes["https://www.empresa.es/bucle"] = redirect("/bucle")
    server.routes["https://www.empresa.es/vacia"] = lambda _: httpx.Response(302)

    results = await add(
        admin,
        "https://www.empresa.es/viejo",
        "https://www.empresa.es/fuera",
        "https://www.empresa.es/ftp",
        "https://www.empresa.es/bucle",
        "https://www.empresa.es/vacia",
        "https://otra.com/a",
    )

    assert errors(results) == [
        None,
        "El dominio otra.com no está entre los permitidos",
        "Solo se admiten direcciones http:// o https://",
        "Demasiadas redirecciones (más de 5)",
        "Redirección sin destino",
        "El dominio otra.com no está entre los permitidos",
    ]
    # Se guarda la dirección indicada; el nombre sale de la final.
    assert results[0]["document"]["source_url"] == "https://www.empresa.es/viejo"
    assert results[0]["document"]["filename"] == "nuevo.html"
    assert "https://otra.com/a" not in {str(r.url) for r in server.requests}


async def test_size_limit_while_downloading(
    app: FastAPI, admin: AsyncClient, server: Server
) -> None:
    await set_config(app, "indexing", upload_max_mb=1)
    big = b"a" * (1024 * 1024 + 1)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"a" * (512 * 1024)

    server.page("https://intranet/declarado.pdf", big, "application/pdf")
    server.routes["https://intranet/sin-longitud.pdf"] = lambda _: httpx.Response(
        200, content=chunks(), headers={"content-type": "application/pdf"}
    )

    results = await add(
        admin, "https://intranet/declarado.pdf", "https://intranet/sin-longitud.pdf"
    )

    assert errors(results) == ["El documento supera el tamaño máximo (1 MB)"] * 2


async def test_slow_servers_time_out(app: FastAPI, admin: AsyncClient, server: Server) -> None:
    await set_config(app, "web", timeout_seconds=1)

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    server.routes["https://intranet/lento"] = slow

    [result] = await add(admin, "https://intranet/lento")

    assert result["error"] == "El servidor no respondió a tiempo (1 s)"


# --- Varias direcciones -------------------------------------------------------------------


async def test_several_urls_keep_their_order(admin: AsyncClient, server: Server) -> None:
    for name in ("a", "b", "c"):
        server.page(
            f"https://intranet/{name}", PAGE.replace("gastos", f"gastos {name}"), "text/html"
        )

    results = await add(
        admin,
        "https://intranet/c",
        "  ",
        "https://intranet/a",
        "https://intranet/c",
        "https://intranet/no",
        " https://intranet/b ",
    )

    assert [(r["filename"], r["error"]) for r in results] == [
        ("https://intranet/c", None),
        ("https://intranet/a", None),
        ("https://intranet/no", "No encontrado (404)"),
        ("https://intranet/b", None),
    ]


async def test_url_requests_are_validated(admin: AsyncClient, client: AsyncClient) -> None:
    assert (await admin.post(URLS, json={"urls": []})).status_code == 422
    many = [f"https://intranet/{n}" for n in range(51)]
    assert (await admin.post(URLS, json={"urls": many})).status_code == 422
    missing = await admin.post(URLS, json={"urls": ["https://intranet/a"], "category_id": 999})
    assert missing.status_code == 422
    await admin.delete("/api/v1/admin/session")
    assert (await client.post(URLS, json={"urls": ["https://intranet/a"]})).status_code == 401
