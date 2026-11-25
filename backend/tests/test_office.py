"""Documentos de Office y OpenDocument (#76)."""

import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.db.models import DocumentKind
from app.services.ingestion.parsers import ParseError, Section, kind_for_filename, parse_document
from app.services.ingestion.titles import extract_title
from app.services.rag import ContextSource
from tests.conftest import ScriptedLLM, parse_sse
from tests.office_factories import (
    make_docx,
    make_odp,
    make_ods,
    make_odt,
    make_pptx,
    make_xlsx,
)


def _parse(path: Path) -> list[Section]:
    kind = kind_for_filename(path.name)
    assert kind is not None
    return parse_document(path, kind)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.docx", DocumentKind.DOCX),
        ("A.PPTX", DocumentKind.PPTX),
        ("a.xlsx", DocumentKind.XLSX),
        ("a.odt", DocumentKind.ODT),
        ("a.odp", DocumentKind.ODP),
        ("a.ods", DocumentKind.ODS),
        ("a.doc", None),
        ("a.ppt", None),
        ("a.xls", None),
    ],
)
def test_modern_formats_are_accepted_and_legacy_ones_are_not(
    name: str, kind: DocumentKind | None
) -> None:
    assert kind_for_filename(name) == kind


@pytest.mark.parametrize("make", [make_docx, make_odt])
def test_text_documents_are_split_by_headings(tmp_path: Path, make: Any) -> None:
    suffix = ".docx" if make is make_docx else ".odt"
    sections = _parse(make(tmp_path / f"normas{suffix}"))

    assert sections[0] == Section(text="Normas generales de la empresa.")
    assert sections[1] == Section(
        text="Se puede teletrabajar dos días a la semana.", heading="Teletrabajo"
    )
    # Tablas y listas en su apartado; una línea por fila.
    assert sections[2].heading == "Teletrabajo > Solicitud"
    assert "La solicitud se hace en el portal del empleado." in sections[2].text
    assert "Día | Horario" in sections[2].text
    assert "Lunes | 8-15" in sections[2].text


def test_docx_closes_subsections_at_a_new_top_heading(tmp_path: Path) -> None:
    sections = _parse(make_docx(tmp_path / "normas.docx"))

    assert sections[-1] == Section(text="La empresa proporciona el portátil.", heading="Equipos")


@pytest.mark.parametrize("make", [make_pptx, make_odp])
def test_presentations_have_one_section_per_slide(tmp_path: Path, make: Any) -> None:
    suffix = ".pptx" if make is make_pptx else ".odp"
    sections = _parse(make(tmp_path / f"plan{suffix}"))

    assert [(s.page, s.heading) for s in sections] == [
        (1, "Plan de formación"),
        (2, "Cursos obligatorios"),
    ]
    assert "Curso 2026" in sections[0].text
    assert "Prevención de riesgos laborales" in sections[1].text


def test_pptx_includes_notes_groups_and_tables_and_skips_hidden_slides(tmp_path: Path) -> None:
    sections = _parse(make_pptx(tmp_path / "plan.pptx"))

    text = sections[1].text
    assert "Duración de ocho horas." in text  # notas
    assert "Texto agrupado" in text
    assert "Primeros auxilios | 4" in text
    assert all("Borrador oculto" not in s.text for s in sections)


def test_xlsx_rows_are_written_with_their_headers(tmp_path: Path) -> None:
    sections = _parse(make_xlsx(tmp_path / "dietas.xlsx"))

    # Solo la hoja visible con contenido.
    assert [s.heading for s in sections] == ["Dietas"]
    assert sections[0].text.split("\n\n") == [
        "País: España; Importe: 53.34; Desde: 2026-01-01; Vigente: Sí",
        "País: Francia; Importe: 70; Vigente: No",
        # Sin cabecera, la columna se nombra por su posición.
        "País: Portugal; Columna 5: nota suelta",
    ]


def test_ods_rows_ignore_repeated_empty_cells_and_hidden_sheets(tmp_path: Path) -> None:
    sections = _parse(make_ods(tmp_path / "dietas.ods"))

    assert [s.heading for s in sections] == ["Dietas"]
    assert sections[0].text.split("\n\n") == [
        "País: España; Importe: 53,34",
        "País: Francia; Importe: 70",
    ]


def test_legacy_or_password_protected_files_are_explained(tmp_path: Path) -> None:
    legacy = tmp_path / "antiguo.docx"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    broken = tmp_path / "roto.xlsx"
    broken.write_bytes(b"no es un zip")
    encrypted = tmp_path / "cifrado.odt"
    with zipfile.ZipFile(encrypted, "w") as archive:
        archive.writestr("META-INF/manifest.xml", "<manifest:encryption-data/>")

    with pytest.raises(ParseError, match="protegido con contraseña o tiene un formato antiguo"):
        _parse(legacy)
    with pytest.raises(ParseError, match="dañado"):
        _parse(broken)
    with pytest.raises(ParseError, match="protegido con contraseña"):
        _parse(encrypted)


@pytest.mark.parametrize(
    ("make", "suffix"),
    [(make_docx, ".docx"), (make_pptx, ".pptx"), (make_xlsx, ".xlsx"), (make_odt, ".odt")],
)
def test_titles_from_document_properties(tmp_path: Path, make: Any, suffix: str) -> None:
    titled = make(tmp_path / f"a{suffix}", "Guía del teletrabajo")
    untitled = make(tmp_path / f"b{suffix}")
    kind = kind_for_filename(titled.name)
    assert kind is not None

    assert extract_title(titled, kind) == "Guía del teletrabajo"
    assert extract_title(untitled, kind) is None


def test_unreadable_office_files_have_no_title(tmp_path: Path) -> None:
    path = tmp_path / "roto.docx"
    path.write_bytes(b"no es un zip")
    assert extract_title(path, DocumentKind.DOCX) is None


# --- De extremo a extremo -----------------------------------------------------------------


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


async def test_upload_index_and_cite_office_documents(
    app: FastAPI, admin: AsyncClient, llm: ScriptedLLM, tmp_path: Path
) -> None:
    files = [
        make_docx(tmp_path / "normas.docx", "Normas de teletrabajo"),
        make_xlsx(tmp_path / "dietas.xlsx"),
        make_odp(tmp_path / "plan.odp"),
    ]
    response = await admin.post(
        "/api/v1/admin/documents",
        files=[("files", (path.name, path.read_bytes())) for path in files],
    )
    assert response.status_code == 200
    await app.state.tasks.drain()

    documents = {d["filename"]: d for d in (await admin.get("/api/v1/admin/documents")).json()}
    assert {name: (d["kind"], d["status"]) for name, d in documents.items()} == {
        "normas.docx": ("docx", "ready"),
        "dietas.xlsx": ("xlsx", "ready"),
        "plan.odp": ("odp", "ready"),
    }
    assert documents["normas.docx"]["title"] == "Normas de teletrabajo"

    llm.chunks = ["Dos días a la semana [1]."]
    chat = await admin.post(
        "/api/v1/chat",
        json={
            "message": "¿Cuántos días se puede teletrabajar a la semana?",
            "document_ids": [documents["normas.docx"]["id"]],
        },
    )
    sources = next(data["sources"] for kind, data in parse_sse(chat.text) if kind == "sources")
    assert sources[0]["title"] == "Normas de teletrabajo"
    assert sources[0]["heading"] == "Teletrabajo"

    # El original se descarga (el navegador no muestra estos formatos).
    original = await admin.get(f"/api/v1/documents/{documents['dietas.xlsx']['id']}/file")
    assert original.status_code == 200
    assert original.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert original.headers["content-disposition"].startswith("attachment;")


async def test_legacy_office_files_are_rejected_on_upload(admin: AsyncClient) -> None:
    response = await admin.post(
        "/api/v1/admin/documents", files=[("files", ("informe.doc", b"\xd0\xcf\x11\xe0"))]
    )

    [result] = response.json()
    assert "Tipo de fichero no admitido" in result["error"]
    assert "Word" in result["error"]


async def test_protected_office_files_end_in_error_with_an_explanation(
    app: FastAPI, admin: AsyncClient
) -> None:
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512
    await admin.post("/api/v1/admin/documents", files=[("files", ("cifrado.xlsx", data))])
    await app.state.tasks.drain()

    [document] = (await admin.get("/api/v1/admin/documents")).json()
    assert document["status"] == "error"
    assert "protegido con contraseña" in document["error"]


def test_slides_are_labelled_as_slides_in_the_prompt() -> None:
    def source(filename: str, page: int, page_end: int | None = None) -> ContextSource:
        return ContextSource(
            number=1,
            document_id="d",
            filename=filename,
            page=page,
            heading=None,
            chunk_index=0,
            score=1.0,
            text="",
            page_end=page_end,
            title="Plan",
        )

    assert source("plan.pptx", 2).label == "Plan, diap. 2"
    assert source("plan.odp", 2, 3).label == "Plan, diaps. 2-3"
    assert source("plan.pdf", 2).label == "Plan, pág. 2"
