"""Subida de un ZIP con varios documentos (#77)."""

import io
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.services.archives import member_name
from tests.conftest import set_config
from tests.factories import MARKDOWN, pdf_bytes
from tests.office_factories import make_docx

URL = "/api/v1/admin/documents"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


@pytest.fixture
async def admin(client: AsyncClient) -> AsyncClient:
    login = await client.post(
        "/api/v1/admin/session", json={"username": "admin", "password": "secreto"}
    )
    assert login.status_code == 200
    return client


def make_zip(
    files: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    encrypted: tuple[str, ...] = (),
    directories: tuple[str, ...] = (),
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for directory in directories:
            archive.writestr(zipfile.ZipInfo(directory), b"")
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            archive.writestr(info, data)
    content = bytearray(buffer.getvalue())
    for name in encrypted:
        # `writestr` no conserva la marca de cifrado: se pone en el directorio central (su
        # registro empieza 46 bytes antes del nombre; las opciones, en el byte 8).
        record = content.rindex(name.encode()) - 46
        assert content[record : record + 4] == b"PK"
        content[record + 8] |= 0x1
    return bytes(content)


async def upload_zip(
    client: AsyncClient, data: bytes, name: str = "documentos.zip", **form: Any
) -> list[dict[str, Any]]:
    response = await client.post(URL, files=[("files", (name, data))], data=form)
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()
    return results


def leftovers(app: FastAPI) -> list[str]:
    """Ficheros temporales que quedan en el directorio de subidas."""
    uploads = app.state.settings.uploads_dir
    return [p.name for p in uploads.iterdir() if p.name.startswith(".")]


def summary(results: list[dict[str, Any]]) -> list[tuple[str, str]]:
    def state(result: dict[str, Any]) -> str:
        if result["error"]:
            return str(result["error"])
        return "duplicado" if result["duplicate"] else "subido"

    return [(r["filename"], state(r)) for r in results]


# --- Nombres ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "flags", "expected"),
    [
        # Con la marca de UTF-8, `zipfile` ya lo decodifica bien.
        ("Guía.pdf", 0x800, "Guía.pdf"),
        # Sin la marca, `zipfile` lo lee como CP437; el ZIP de Windows en español usa CP850.
        ("Guía ÁREA.pdf".encode("cp850").decode("cp437"), 0, "Guía ÁREA.pdf"),
        # UTF-8 sin la marca (algunas herramientas).
        ("Guía.pdf".encode().decode("cp437"), 0, "Guía.pdf"),
        ("plain.pdf", 0, "plain.pdf"),
    ],
)
def test_member_names_are_decoded(raw: str, flags: int, expected: str) -> None:
    info = zipfile.ZipInfo(raw)
    info.flag_bits = flags
    assert member_name(info) == expected


async def test_names_without_the_utf8_flag_keep_their_accents(
    app: FastAPI, admin: AsyncClient
) -> None:
    # `zipfile` siempre marca los nombres no ASCII como UTF-8: se escriben con nombres
    # ASCII de la misma longitud y se cambian los bytes (como un ZIP de Windows en CP850).
    data = make_zip({"carpeta/GuXa.md": b"# Hola", "XREA.md": b"# Adios"})
    data = data.replace(b"GuXa", "Guía".encode("cp850")).replace(b"XREA", "ÁREA".encode("cp850"))

    results = await upload_zip(admin, data)

    assert summary(results) == [("carpeta/Guía.md", "subido"), ("ÁREA.md", "subido")]
    assert {r["document"]["filename"] for r in results} == {"Guía.md", "ÁREA.md"}


# --- Contenido ----------------------------------------------------------------------------


async def test_zip_contents_are_uploaded_one_by_one(
    app: FastAPI, admin: AsyncClient, tmp_path: Path
) -> None:
    await set_config(app, "indexing", upload_max_mb=1)
    rrhh = (await admin.post("/api/v1/admin/categories", json={"name": "RR. HH."})).json()["id"]
    data = make_zip(
        {
            "guia.md": MARKDOWN.encode(),
            "carpeta/manual.pdf": pdf_bytes(tmp_path, ["Manual del empleado"]),
            "carpeta/normas.docx": make_docx(tmp_path / "normas.docx").read_bytes(),
            "copia de la guia.md": MARKDOWN.encode(),
            "notas.txt": b"texto plano",
            "vacio.md": b"",
            "anidado.zip": b"PK",
            "cifrado.md": b"# Secreto",
            "__MACOSX/._guia.md": b"basura",
            ".DS_Store": b"basura",
            "carpeta/Thumbs.db": b"basura",
            ".oculta/nota.md": b"# Oculta",
        },
        encrypted=("cifrado.md",),
        directories=("carpeta/",),
    )

    results = await upload_zip(admin, data, category_id=str(rrhh), use_filename="true")
    await app.state.tasks.drain()

    assert all(r["archive"] == "documentos.zip" for r in results)
    assert summary(results) == [
        ("guia.md", "subido"),
        ("carpeta/manual.pdf", "subido"),
        ("carpeta/normas.docx", "subido"),
        ("copia de la guia.md", "duplicado"),
        ("notas.txt", results[4]["error"]),
        ("vacio.md", "El fichero está vacío"),
        ("anidado.zip", "No se admiten ZIP dentro de otro ZIP"),
        ("cifrado.md", "Está protegido con contraseña dentro del ZIP"),
    ]
    assert "Tipo de fichero no admitido" in results[4]["error"]
    documents = (await admin.get(URL)).json()
    assert sorted(
        (d["filename"], d["title"], d["category_id"], d["status"]) for d in documents
    ) == [
        ("guia.md", "guia.md", rrhh, "ready"),
        ("manual.pdf", "manual.pdf", rrhh, "ready"),
        ("normas.docx", "normas.docx", rrhh, "ready"),
    ]
    assert leftovers(app) == []


async def test_the_size_limit_applies_to_each_file_not_to_the_zip(
    app: FastAPI, admin: AsyncClient
) -> None:
    await set_config(app, "indexing", upload_max_mb=1)
    # Dos ficheros de 0,8 MB sin comprimir: el ZIP supera 1 MB y cada fichero no.
    files = {f"parte{n}.md": b"# Parte\n\n" + os.urandom(400 * 1024).hex().encode() for n in (1, 2)}
    data = make_zip(files, compression=zipfile.ZIP_STORED)
    assert len(data) > 1024 * 1024

    results = await upload_zip(admin, data)

    assert summary(results) == [("parte1.md", "subido"), ("parte2.md", "subido")]


async def test_zip_bombs_stop_at_the_size_limit(app: FastAPI, admin: AsyncClient) -> None:
    await set_config(app, "indexing", upload_max_mb=1)
    # 20 MB de espacios comprimidos en unos pocos KB.
    data = make_zip({"bomba.md": b" " * (20 * 1024 * 1024), "bien.md": b"# Bien"})
    assert len(data) < 100 * 1024

    results = await upload_zip(admin, data)

    assert summary(results) == [
        ("bomba.md", "El fichero supera el tamaño máximo (1 MB)"),
        ("bien.md", "subido"),
    ]
    assert leftovers(app) == []


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"no es un zip", "El ZIP está dañado o no es un fichero ZIP"),
        (make_zip({}), "El ZIP no contiene ningún fichero"),
        (make_zip({"__MACOSX/._a.md": b"x"}), "El ZIP no contiene ningún fichero"),
    ],
)
async def test_unreadable_or_empty_zips_are_reported(
    app: FastAPI, admin: AsyncClient, data: bytes, error: str
) -> None:
    results = await upload_zip(admin, data, name="malo.ZIP")

    assert results == [
        {
            "filename": "malo.ZIP",
            "archive": None,
            "document": None,
            "duplicate": False,
            "error": error,
        }
    ]
    assert leftovers(app) == []


async def test_corrupt_files_inside_the_zip_are_reported(admin: AsyncClient) -> None:
    data = bytearray(make_zip({"a.md": b"# Hola " * 1000, "b.md": b"# Bien"}))
    # Se estropean los datos comprimidos del primer fichero (tras su cabecera local).
    start = data.index(b"a.md") + len(b"a.md") + 10
    data[start : start + 20] = b"\0" * 20

    results = await upload_zip(admin, bytes(data))

    assert summary(results) == [
        ("a.md", "El fichero está dañado dentro del ZIP"),
        ("b.md", "subido"),
    ]


async def test_zips_and_files_can_be_uploaded_together(admin: AsyncClient) -> None:
    response = await admin.post(
        URL,
        files=[
            ("files", ("suelto.md", b"# Suelto")),
            ("files", ("lote.zip", make_zip({"dentro.md": b"# Dentro"}))),
        ],
    )

    assert [(r["filename"], r["archive"], r["error"]) for r in response.json()] == [
        ("suelto.md", None, None),
        ("dentro.md", "lote.zip", None),
    ]


async def test_damaged_headers_and_unsupported_compression_are_reported(
    admin: AsyncClient,
) -> None:
    data = bytearray(make_zip({"a.md": b"# A", "b.md": b"# B", "c.md": b"# C"}))
    # a.md: firma de su cabecera local estropeada.
    data[0:4] = b"XXXX"
    # b.md: método de compresión 9 (Deflate64) en su cabecera local y en el directorio central.
    local = data.index(b"b.md") - 30
    central = data.rindex(b"b.md") - 46
    data[local + 8] = 9
    data[central + 10] = 9

    results = await upload_zip(admin, bytes(data))

    assert summary(results) == [
        ("a.md", "El fichero está dañado dentro del ZIP"),
        ("b.md", "Método de compresión no admitido: vuelve a crear el ZIP"),
        ("c.md", "subido"),
    ]
