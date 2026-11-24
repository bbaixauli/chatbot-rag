import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Document
from app.diagnose import (
    cosine,
    diagnose_document,
    diagnose_query,
    estimate_truncation,
    find_documents,
    main,
)
from app.main import create_app
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.ingestion.preprocess import looks_like_toc
from tests.conftest import set_config
from tests.factories import MARKDOWN

CREDENTIALS = {"username": "admin", "password": "secreto"}

TOC = """Índice
1. Introducción ........ 3
2. Vacaciones .......... 5
2.1 Solicitud ......... 6
3. Teletrabajo ......... 9
4. Gastos de viaje ... 12
Anexos"""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    return settings


class BatchSensitiveEmbeddings(FakeEmbeddings):
    """Simula un servidor que devuelve vectores erróneos al recibir varios textos a la vez."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(texts) == 1:
            return await super().embed(texts)
        return await super().embed([f"{text} ruido aleatorio del lote" for text in reversed(texts)])


async def _upload(
    app: FastAPI, client: AsyncClient, name: str = "guia.md", content: str = MARKDOWN
) -> Document:
    assert (await client.post("/api/v1/admin/session", json=CREDENTIALS)).status_code == 200
    response = await client.post(
        "/api/v1/admin/documents", files=[("files", (name, content.encode()))]
    )
    assert response.status_code == 200
    await app.state.tasks.drain()
    [document] = await find_documents(app.state.sessionmaker, [name], all_documents=False)
    return document


def test_cosine_and_toc_detection() -> None:
    assert cosine([1, 0], [1, 0]) == pytest.approx(1)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0)
    assert cosine([0, 0], [1, 0]) == 0
    assert cosine([1, 0], [1, 0, 0]) == 0
    assert looks_like_toc(TOC)
    assert not looks_like_toc(MARKDOWN)
    assert not looks_like_toc("1. Uno 1\n2. Dos 2")  # demasiado corto para decidir


async def test_healthy_document(app: FastAPI, client: AsyncClient, settings: Settings) -> None:
    document = await _upload(app, client)

    report = await diagnose_document(
        document,
        settings,
        app.state.config.current,
        app.state.vector_store,
        app.state.embeddings,
        batch_check=True,
    )

    assert report.ok, report.problems
    assert report.stored_chunks > 0
    assert report.stored_chunks == report.db_chunks == report.expected_chunks
    assert report.checked_chunks == report.stored_chunks
    assert all(check.similarity == pytest.approx(1) for check in report.chunks)
    assert all(check.batch_similarity == pytest.approx(1) for check in report.chunks)


async def test_detects_wrong_and_missing_vectors(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client)
    store = app.state.vector_store
    stored = await store.document_chunks(document.id)
    assert len(stored) >= 3
    # El fragmento 0 se guarda con el vector de otro texto y falta el último.
    wrong = Chunk(0, stored[0].text, stored[0].page, stored[0].heading)
    [vector] = await app.state.embeddings.embed(["un texto completamente distinto"])
    await store.add(document.id, document.filename, [wrong], [vector])
    await store.delete_chunks_from(document.id, len(stored) - 1)

    report = await diagnose_document(
        document, settings, app.state.config.current, store, app.state.embeddings
    )

    assert not report.ok
    problems = "\n".join(report.problems)
    assert f"indica {len(stored)} fragmentos y el índice tiene {len(stored) - 1}" in problems
    assert "El texto indexado no coincide" in problems
    assert "1 fragmentos con un vector guardado distinto" in problems
    assert report.chunks[0].similarity is not None
    assert report.chunks[0].similarity < 0.99


async def test_detects_duplicate_vectors_and_toc(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client)
    store = app.state.vector_store
    stored = await store.document_chunks(document.id)
    [toc_vector] = await app.state.embeddings.embed([TOC])
    # Dos textos distintos con el mismo vector (como un lote defectuoso) y uno con un índice.
    await store.add(
        document.id,
        document.filename,
        [Chunk(0, TOC, 1, None), Chunk(1, stored[1].text, None, stored[1].heading)],
        [toc_vector, toc_vector],
    )

    report = await diagnose_document(
        document, settings, app.state.config.current, store, app.state.embeddings
    )

    assert "Fragmentos 0 y 1: textos distintos con el mismo vector" in report.problems
    assert "Fragmentos con aspecto de índice: [0]" in report.notes
    assert report.chunks[0].toc


async def test_batch_check_detects_inconsistent_server(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client)

    report = await diagnose_document(
        document,
        settings,
        app.state.config.current,
        app.state.vector_store,
        BatchSensitiveEmbeddings(),
        batch_check=True,
        max_chunks=2,
    )

    assert report.checked_chunks == 2
    assert "Solo se comprueban los 2 primeros fragmentos" in report.notes
    # Lo guardado coincide con el cálculo individual; los lotes no.
    assert all(check.similarity == pytest.approx(1) for check in report.chunks)
    assert any("en lotes de" in problem for problem in report.problems)


async def test_document_without_original_file(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client)
    app.state.documents.file_path(document).unlink()

    report = await diagnose_document(
        document, settings, app.state.config.current, app.state.vector_store, app.state.embeddings
    )

    assert any("No se encuentra el fichero original" in p for p in report.problems)


async def test_query_marks_chunks_used_by_chat(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    await _upload(app, client)
    await set_config(app, "rag", top_k=2, min_score=0.0)

    hits = await diagnose_query(
        "días de vacaciones",
        app.state.config.current,
        app.state.vector_store,
        app.state.embeddings,
        top=10,
    )

    assert len(hits) > 2
    assert [item.used for item in hits[:2]] == [True, True]
    assert not any(item.used for item in hits[2:])
    assert "top-k" in hits[2].reason
    assert "vacaciones" in hits[0].hit.text


async def test_find_documents_by_id_name_or_all(app: FastAPI, client: AsyncClient) -> None:
    document = await _upload(app, client)
    sessionmaker = app.state.sessionmaker

    assert [d.id for d in await find_documents(sessionmaker, [document.id], False)] == [document.id]
    assert [d.id for d in await find_documents(sessionmaker, ["gui"], False)] == [document.id]
    assert [d.id for d in await find_documents(sessionmaker, [], True)] == [document.id]


async def _prepare(settings: Settings) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await application.state.documents.upload("guia.md", _Reader(MARKDOWN), "admin")
        await application.state.tasks.drain()


class _Reader:
    def __init__(self, text: str) -> None:
        self._data = text.encode()

    async def read(self, size: int = -1) -> bytes:
        data, self._data = self._data, b""
        return data


def test_cli(settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(_prepare(settings))

    assert main(["index", "--all", "--batch-check", "-v"], settings) == 0
    output = capsys.readouterr().out
    assert "guia.md" in output
    assert "Sin anomalías" in output
    assert "1 documentos revisados, 0 con anomalías" in output

    assert main(["query", "días de vacaciones", "--top", "3"], settings) == 0
    output = capsys.readouterr().out
    assert "Pregunta: días de vacaciones" in output
    assert "El chat usaría" in output

    assert main(["index", "no-existe"], settings) == 1
    assert "No hay ningún documento" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        main(["index"], settings)


# Índice tal como lo extrae PyMuPDF de muchos PDF: el número de página en otra línea.
TOC_SPLIT_LINES = """Índice
1. Introducción ...............
3
2. Vacaciones ...............
5
2.1 Solicitud
6
3. Teletrabajo
9"""

LONG_SECTION = " ".join(
    f"El apartado {n} describe el procedimiento número {n} con sus requisitos, plazos y "
    f"documentación necesaria para la tramitación del expediente."
    for n in range(1, 25)
)
LONG_MARKDOWN = f"# Procedimientos\n\n{LONG_SECTION}\n"

TRUNCATION_LIMIT = 300


class TruncatingEmbeddings(FakeEmbeddings):
    """Simula un servidor con contexto corto: ignora el texto a partir de un límite."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await super().embed([text[:TRUNCATION_LIMIT] for text in texts])


def test_toc_with_page_numbers_on_their_own_lines() -> None:
    assert looks_like_toc(TOC_SPLIT_LINES)
    # Una página normal con su número de página al pie no es un índice.
    body = "\n".join(
        [
            "Las vacaciones se solicitan en el portal del empleado.",
            "La solicitud debe hacerse con 15 días de antelación.",
            "El responsable la aprueba o la rechaza en un plazo de 5 días.",
            "Si se rechaza, el empleado puede proponer otras fechas.",
            "Los días no disfrutados se pierden al final del año.",
            "12",
        ]
    )
    assert not looks_like_toc(body)


async def test_truncation_check_without_truncation(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client, "largo.md", LONG_MARKDOWN)

    report = await diagnose_document(
        document,
        settings,
        app.state.config.current,
        app.state.vector_store,
        app.state.embeddings,
        truncation_check=True,
    )

    assert report.ok, report.problems
    assert report.truncation_chars is None
    assert all(c.probe_similarity is not None for c in report.chunks)
    assert all(c.probe_similarity < 0.999 for c in report.chunks if c.probe_similarity)


async def test_truncation_check_detects_and_estimates_the_limit(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    truncating = TruncatingEmbeddings()
    app.state.documents._embeddings = truncating  # el servidor ya recortaba al indexar
    document = await _upload(app, client, "largo.md", LONG_MARKDOWN)

    report = await diagnose_document(
        document,
        settings,
        app.state.config.current,
        app.state.vector_store,
        truncating,
        truncation_check=True,
    )

    # Los vectores guardados coinciden con los actuales: sin esta prueba no se vería nada.
    assert all(c.similarity == pytest.approx(1) for c in report.chunks)
    assert report.truncation_chars is not None
    assert TRUNCATION_LIMIT - 20 <= report.truncation_chars <= TRUNCATION_LIMIT
    [problem] = report.problems
    assert "recorta los textos" in problem
    assert f"unos {report.truncation_chars} caracteres" in problem


async def test_estimate_truncation_returns_whole_text_without_truncation() -> None:
    embeddings = FakeEmbeddings()
    text = "uno dos tres cuatro cinco seis siete ocho nueve diez"
    [full] = await embeddings.embed([text])

    assert await estimate_truncation(embeddings, text, full) >= len(text) - len("diez")


async def test_query_full_ranking_for_a_document(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    await _upload(app, client)
    other = await _upload(app, client, "largo.md", LONG_MARKDOWN)
    total = await app.state.vector_store.count()

    hits = await diagnose_query(
        "días de vacaciones",
        app.state.config.current,
        app.state.vector_store,
        app.state.embeddings,
        top=1,
        full_ranking=True,
    )

    assert len(hits) == total
    assert [item.rank for item in hits] == list(range(1, total + 1))
    assert any(item.hit.document_id == other.id for item in hits)


def test_cli_document_ranking_and_truncation(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(_prepare(settings))

    assert main(["query", "teletrabajo", "--top", "2", "--document", "guia"], settings) == 0
    output = capsys.readouterr().out
    assert "== guia.md (" in output
    assert "el mejor queda en el puesto 1 de" in output
    assert "Sus 2 mejores fragmentos:" in output

    assert main(["index", "guia", "--truncation-check", "-v"], settings) == 0
    output = capsys.readouterr().out
    assert "recorte " in output
    assert "Sin anomalías" in output


async def test_detects_documents_indexed_with_another_preprocessing(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    document = await _upload(app, client)
    await set_config(app, "indexing", context_prefix=False)

    report = await diagnose_document(
        document,
        settings,
        app.state.config.current,
        app.state.vector_store,
        app.state.embeddings,
    )

    assert any("otro preprocesado" in problem for problem in report.problems)
    # El vector guardado se calculó con el título delante: ya no coincide.
    assert any("vector guardado distinto" in problem for problem in report.problems)


async def test_query_lowers_tables_of_contents(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    await _upload(app, client, "indice.md", f"# Guía\n\n{TOC}\n\n# Vacaciones\n\n{MARKDOWN}")
    await set_config(app, "rag", top_k=10, min_score=0.0, toc_score_factor=0.0)

    hits = await diagnose_query(
        TOC, app.state.config.current, app.state.vector_store, app.state.embeddings, top=10
    )

    # Sin restarle peso, el índice sería el primero: la pregunta es su propio texto.
    toc = next(item for item in hits if item.toc)
    assert toc.hit.score == 0.0
    assert hits[0] is not toc


def test_cli_query_with_scope(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    asyncio.run(_prepare(settings))

    assert main(["query", "vacaciones", "--scope", "guia.md"], settings) == 0
    output = capsys.readouterr().out
    assert "Búsqueda limitada a: guia.md" in output
    assert "El chat usaría" in output

    assert main(["query", "vacaciones", "--scope", "no-existe"], settings) == 1
    assert "No hay ningún documento" in capsys.readouterr().err
