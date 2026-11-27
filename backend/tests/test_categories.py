"""Título y categorías de los documentos (#75)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.services.embeddings import FakeEmbeddings
from tests.conftest import ScriptedLLM, parse_sse, set_config
from tests.factories import MARKDOWN, pdf_bytes

DOCUMENTS = "/api/v1/admin/documents"
CATEGORIES = "/api/v1/admin/categories"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    settings.chunk_size_chars = 300
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Textos con los que se calculan los vectores."""
    texts: list[str] = []
    original = FakeEmbeddings.embed

    async def embed(self: FakeEmbeddings, batch: Sequence[str]) -> list[list[float]]:
        texts.extend(batch)
        return await original(self, batch)

    monkeypatch.setattr(FakeEmbeddings, "embed", embed)
    return texts


async def upload(
    client: AsyncClient, name: str, data: bytes, app: FastAPI, **form: Any
) -> dict[str, Any]:
    response = await client.post(
        DOCUMENTS,
        files=[("files", (name, data))],
        data={
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in form.items()
        },
    )
    assert response.status_code == 200, response.text
    await app.state.tasks.drain()
    [result] = response.json()
    document: dict[str, Any] = result["document"]
    fresh: dict[str, Any] = (await client.get(f"{DOCUMENTS}/{document['id']}")).json()
    return fresh


async def category(client: AsyncClient, name: str) -> int:
    response = await client.post(CATEGORIES, json={"name": name})
    assert response.status_code == 201, response.text
    category_id: int = response.json()["id"]
    return category_id


# --- Categorías ---------------------------------------------------------------------------


async def test_categories_require_admin(client: AsyncClient) -> None:
    assert (await client.get(CATEGORIES)).status_code == 401
    assert (await client.post(CATEGORIES, json={"name": "RR. HH."})).status_code == 401


async def test_create_rename_list_and_delete_categories(app: FastAPI, admin: AsyncClient) -> None:
    rrhh = await category(admin, "  Recursos   humanos ")
    await category(admin, "Calidad")
    await upload(admin, "guia.md", MARKDOWN.encode(), app, category_id=rrhh)

    listed = (await admin.get(CATEGORIES)).json()
    assert listed == [
        {"id": listed[0]["id"], "name": "Calidad", "documents": 0},
        {"id": rrhh, "name": "Recursos humanos", "documents": 1},
    ]

    renamed = await admin.patch(f"{CATEGORIES}/{rrhh}", json={"name": "Personas"})
    assert renamed.status_code == 200
    assert renamed.json() == {"id": rrhh, "name": "Personas", "documents": 1}

    assert (await admin.delete(f"{CATEGORIES}/{rrhh}")).status_code == 204
    assert [c["name"] for c in (await admin.get(CATEGORIES)).json()] == ["Calidad"]
    # Los documentos se conservan, sin categoría.
    [document] = (await admin.get(DOCUMENTS)).json()
    assert document["category_id"] is None


async def test_category_names_are_unique_ignoring_case(admin: AsyncClient) -> None:
    calidad = await category(admin, "Calidad")
    other = await category(admin, "Otra")

    duplicate = await admin.post(CATEGORIES, json={"name": "CALIDAD"})
    assert duplicate.status_code == 409
    assert "Calidad" in duplicate.json()["detail"]
    assert (await admin.patch(f"{CATEGORIES}/{other}", json={"name": "calidad"})).status_code == 409
    # Cambiar solo las mayúsculas de la propia categoría sí se puede.
    assert (
        await admin.patch(f"{CATEGORIES}/{calidad}", json={"name": "CALIDAD"})
    ).status_code == 200
    assert (await admin.post(CATEGORIES, json={"name": "   "})).status_code == 422
    await category(admin, "Árbol")
    assert (await admin.post(CATEGORIES, json={"name": "ÁRBOL"})).status_code == 409
    assert (await admin.patch(f"{CATEGORIES}/999", json={"name": "X"})).status_code == 404
    assert (await admin.delete(f"{CATEGORIES}/999")).status_code == 404


# --- Título y categoría al subir -----------------------------------------------------------


async def test_title_from_metadata_or_filename(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    titled = pdf_bytes(tmp_path, ["Texto del manual"], title="Manual del empleado")
    untitled = pdf_bytes(tmp_path, ["Otro texto"], title="Microsoft Word - otro.docx")

    from_metadata = await upload(admin, "manual.pdf", titled, app)
    fallback = await upload(admin, "otro.pdf", untitled, app)

    assert from_metadata["title"] == "Manual del empleado"
    assert from_metadata["filename"] == "manual.pdf"
    assert fallback["title"] == "otro.pdf"


async def test_upload_can_use_the_filename_and_a_category(app: FastAPI, admin: AsyncClient) -> None:
    rrhh = await category(admin, "Recursos humanos")

    document = await upload(
        admin, "guia.md", MARKDOWN.encode(), app, use_filename=True, category_id=rrhh
    )

    assert document["title"] == "guia.md"
    assert document["category_id"] == rrhh
    assert document["status"] == "ready"
    assert document["outdated"] is False


async def test_upload_to_a_missing_category_is_rejected(admin: AsyncClient) -> None:
    response = await admin.post(
        DOCUMENTS, files=[("files", ("guia.md", MARKDOWN.encode()))], data={"category_id": "999"}
    )
    assert response.status_code == 422
    assert (await admin.get(DOCUMENTS)).json() == []


async def test_title_category_and_filename_go_into_the_vectors(
    app: FastAPI, admin: AsyncClient, embedded: list[str], tmp_path: Path
) -> None:
    rrhh = await category(admin, "Recursos humanos")
    data = pdf_bytes(tmp_path, ["Texto del manual"], title="Manual del empleado")

    await upload(admin, "NOR-013_manual.pdf", data, app, category_id=rrhh)

    assert embedded == [
        "Documento: Manual del empleado\nFichero: NOR-013 manual\n"
        "Categoría: Recursos humanos\n\nTexto del manual"
    ]


async def test_documents_titled_with_their_filename_index_as_before(
    app: FastAPI, admin: AsyncClient, embedded: list[str], tmp_path: Path
) -> None:
    # Como los documentos anteriores a #75: el texto de los vectores no cambia.
    await upload(admin, "NOR-013_manual.pdf", pdf_bytes(tmp_path, ["Texto"]), app)

    assert embedded == ["Documento: NOR-013 manual\n\nTexto"]


# --- Cambiar el título o la categoría ------------------------------------------------------


async def test_edit_title_and_category(app: FastAPI, admin: AsyncClient) -> None:
    rrhh = await category(admin, "Recursos humanos")
    document = await upload(admin, "guia.md", MARKDOWN.encode(), app)
    url = f"{DOCUMENTS}/{document['id']}"

    edited = await admin.patch(url, json={"title": "  Vacaciones  2026 ", "category_id": rrhh})
    assert edited.status_code == 200
    assert (edited.json()["title"], edited.json()["category_id"]) == ("Vacaciones 2026", rrhh)

    # Sin `category_id`, la categoría no cambia; con `null`, se quita.
    kept = await admin.patch(url, json={"title": "Vacaciones"})
    assert kept.json()["category_id"] == rrhh
    cleared = await admin.patch(url, json={"category_id": None})
    assert (cleared.json()["title"], cleared.json()["category_id"]) == ("Vacaciones", None)

    assert (await admin.patch(url, json={"title": "   "})).status_code == 422
    assert (await admin.patch(url, json={"category_id": 999})).status_code == 422
    assert (await admin.patch(f"{DOCUMENTS}/nada", json={"title": "X"})).status_code == 404


async def test_changing_title_or_category_leaves_the_document_outdated(
    app: FastAPI, admin: AsyncClient
) -> None:
    rrhh = await category(admin, "Recursos humanos")
    document = await upload(admin, "guia.md", MARKDOWN.encode(), app, category_id=rrhh)
    url = f"{DOCUMENTS}/{document['id']}"

    async def outdated() -> tuple[bool, int]:
        listed = (await admin.get(DOCUMENTS)).json()
        stats = (await admin.get(f"{DOCUMENTS}/stats")).json()
        return listed[0]["outdated"], stats["outdated"]

    assert await outdated() == (False, 0)
    assert (await admin.patch(url, json={"title": "Vacaciones"})).json()["outdated"] is True
    assert await outdated() == (True, 1)

    scheduled = await admin.post(f"{DOCUMENTS}/reindex-outdated")
    assert scheduled.status_code == 202
    assert scheduled.json() == {"scheduled": 1}
    await app.state.tasks.drain()
    assert await outdated() == (False, 0)

    # Renombrar o borrar la categoría también.
    await admin.patch(f"{CATEGORIES}/{rrhh}", json={"name": "Personas"})
    assert await outdated() == (True, 1)
    await admin.post(f"{DOCUMENTS}/reindex-outdated")
    await app.state.tasks.drain()
    await admin.delete(f"{CATEGORIES}/{rrhh}")
    assert await outdated() == (True, 1)


async def test_title_does_not_matter_without_the_context_prefix(
    app: FastAPI, admin: AsyncClient
) -> None:
    await set_config(app, "indexing", context_prefix=False)
    document = await upload(admin, "guia.md", MARKDOWN.encode(), app)

    edited = await admin.patch(f"{DOCUMENTS}/{document['id']}", json={"title": "Otro"})

    assert edited.json()["outdated"] is False


# --- Referencias con el título ---------------------------------------------------------------


async def ask(client: AsyncClient, message: str, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post("/api/v1/chat", json={"message": message, **body})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def test_references_use_the_current_title(
    app: FastAPI, admin: AsyncClient, llm: ScriptedLLM
) -> None:
    document = await upload(admin, "guia.md", MARKDOWN.encode(), app, use_filename=True)
    llm.chunks = ["Hay 23 días de vacaciones [1]."]

    events = await ask(admin, "¿Cuántos días de vacaciones tengo?", document_ids=[document["id"]])

    assert "Fuente: guia.md" in llm.calls[-1][0].content
    [source] = next(data["sources"] for kind, data in events if kind == "sources")
    assert (source["filename"], source["title"]) == ("guia.md", "guia.md")
    scope = next(data for kind, data in events if kind == "scope")
    assert scope["documents"][0]["title"] == "guia.md"

    await admin.patch(f"{DOCUMENTS}/{document['id']}", json={"title": "Vacaciones"})

    # Las respuestas ya guardadas muestran el título nuevo…
    conversation_id = events[0][1]["id"]
    messages = (await admin.get(f"/api/v1/conversations/{conversation_id}/messages")).json()
    answer = messages[-1]
    assert answer["sources"][0]["title"] == "Vacaciones"
    assert answer["sources"][0]["filename"] == "guia.md"
    assert answer["scope"]["documents"][0]["title"] == "Vacaciones"
    # …y las nuevas lo usan en el prompt y en la lista de documentos del chat.
    await ask(admin, "¿Y cuántos días?", conversation_id=conversation_id)
    assert "Fuente: Vacaciones" in llm.calls[-1][0].content
    listed = (await admin.get("/api/v1/documents")).json()
    assert [(d["title"], d["category"]) for d in listed] == [("Vacaciones", None)]


async def test_deleted_documents_keep_their_filename_in_references(
    app: FastAPI, admin: AsyncClient, llm: ScriptedLLM
) -> None:
    document = await upload(admin, "guia.md", MARKDOWN.encode(), app)
    llm.chunks = ["Hay 23 días [1]."]
    events = await ask(admin, "¿Cuántos días de vacaciones tengo?", document_ids=[document["id"]])

    await admin.delete(f"{DOCUMENTS}/{document['id']}")

    messages = (await admin.get(f"/api/v1/conversations/{events[0][1]['id']}/messages")).json()
    assert messages[-1]["sources"][0]["title"] == "guia.md"


async def test_questions_can_name_a_document_by_its_title(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    data = pdf_bytes(tmp_path, ["Las cuentas se bloquean."], title="Normativa de control de acceso")
    document = await upload(admin, "nor13.pdf", data, app)
    await upload(admin, "guia.md", MARKDOWN.encode(), app)

    events = await ask(admin, "¿Qué dice la normativa de control de acceso sobre los bloqueos?")

    scope = next(data for kind, data in events if kind == "scope")
    assert scope == {
        "documents": [
            {
                "id": document["id"],
                "filename": "nor13.pdf",
                "title": "Normativa de control de acceso",
            }
        ],
        "categories": [],
        "source": "detected",
    }


async def test_public_list_includes_the_category(app: FastAPI, admin: AsyncClient) -> None:
    rrhh = await category(admin, "Recursos humanos")
    await upload(admin, "guia.md", MARKDOWN.encode(), app, category_id=rrhh)

    [listed] = (await admin.get("/api/v1/documents")).json()

    assert listed["category"] == "Recursos humanos"
    assert listed["title"] == "Guía de vacaciones"
