"""El prompt se ajusta a la ventana de contexto del modelo (#73)."""

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.runtime_config import LlmConfig, RuntimeConfig, StaticConfig
from app.db.models import QuestionLog
from app.services.chat import TOO_LARGE_MESSAGE
from app.services.dashboard import trim_label
from app.services.embeddings import FakeEmbeddings
from app.services.ingestion.chunker import Chunk
from app.services.llm import ChatMessage
from app.services.prompt_budget import (
    MESSAGE_OVERHEAD_TOKENS,
    PromptTrim,
    estimate_tokens,
    fit_recent,
    messages_tokens,
    server_trimmed,
    tokens_to_chars,
    underestimated,
)
from app.services.rag import FULL_DOCUMENT_NOTE, SOURCE_OVERHEAD_CHARS, Retriever
from app.services.vectorstore import ChromaVectorStore
from tests.conftest import ScriptedLLM, parse_sse, set_config

# Cinco apartados de unos 400 caracteres: con fragmentos de 450, uno por apartado.
SECTIONS = [
    ("Vacaciones", "Los empleados tienen 23 días laborables de vacaciones al año. "),
    ("Teletrabajo", "Se permite teletrabajar dos días por semana con acuerdo previo. "),
    ("Dietas", "La dieta diaria nacional es de 53,34 euros por día de viaje. "),
    ("Formación", "Cada persona dispone de 40 horas anuales de formación pagada. "),
    ("Horario", "El horario flexible permite entrar entre las 7:30 y las 9:30. "),
]
DOCUMENT = "\n\n".join(f"# {title}\n\n{text * 6}" for title, text in SECTIONS)


class Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        data, self._data = self._data, b""
        return data


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.chunk_size_chars = 450
    settings.chunk_overlap_chars = 0
    settings.rag_min_score = 0
    settings.rag_top_k = 5
    settings.rag_full_document_max_chars = 100_000
    return settings


async def _index(app: FastAPI, content: str = DOCUMENT) -> str:
    result = await app.state.documents.upload("normas.md", Bytes(content.encode()), "admin")
    await app.state.tasks.drain()
    document_id: str = result.document.id
    return document_id


async def _ask(client: AsyncClient, message: str, **body: Any) -> list[tuple[str, Any]]:
    response = await client.post("/api/v1/chat", json={"message": message, **body})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def _last_log(app: FastAPI) -> QuestionLog:
    async with app.state.sessionmaker() as session:
        rows: list[QuestionLog] = list(
            await session.scalars(select(QuestionLog).order_by(QuestionLog.id))
        )
    return rows[-1]


async def _window(app: FastAPI, tokens: int, reserve: int = 100) -> int:
    await set_config(app, "llm", context_window_tokens=tokens, response_reserve_tokens=reserve)
    return tokens - reserve


# --- Estimación ---------------------------------------------------------------------------


def test_estimates_and_history_fitting() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 7) == 2
    assert tokens_to_chars(10) == 35
    assert tokens_to_chars(-5) == 0
    messages = [ChatMessage("user", "a" * 35) for _ in range(4)]
    cost = 10 + MESSAGE_OVERHEAD_TOKENS
    assert messages_tokens(messages) == 4 * cost

    kept, dropped = fit_recent(messages, 2 * cost + 1)
    assert (kept, dropped) == (messages[2:], 2)
    assert fit_recent(messages, 0) == ([], 4)


def test_trim_payload_and_server_count() -> None:
    assert PromptTrim().payload is None
    assert PromptTrim(full_document=True, history=2).payload == {
        "full_document": True,
        "history": 2,
    }
    assert server_trimmed(1000, 500)
    assert not server_trimmed(1000, 800)
    assert not server_trimmed(1000, None)
    assert not server_trimmed(0, 10)
    assert PromptTrim(server=True, underestimated=True).payload == {
        "server": True,
        "underestimated": True,
    }


def test_window_full_and_underestimate() -> None:
    # Con la ventana configurada, llenarla es un recorte aunque se contara más de lo estimado
    # (la estimación se quedó corta, #101).
    assert server_trimmed(7000, 8100, window=8192)
    assert server_trimmed(7000, 8029, window=8192)  # 98 % de 8192: 8028,16
    assert not server_trimmed(7000, 8000, window=8192)
    assert not server_trimmed(7000, 8100)
    assert not server_trimmed(7000, None, window=8192)
    # Por encima del límite (ventana menos reserva), la estimación se quedó corta.
    assert underestimated(7200, 7168)
    assert not underestimated(7168, 7168)
    assert not underestimated(7200, None)
    assert not underestimated(None, 7168)


def test_window_configuration() -> None:
    llm = RuntimeConfig.from_settings(Settings()).llm
    assert (llm.context_window_tokens, llm.response_reserve_tokens) == (0, 1024)
    assert llm.prompt_max_tokens is None
    assert llm.model_copy(update={"context_window_tokens": 8192}).prompt_max_tokens == 7168
    with pytest.raises(ValidationError, match="menor que la ventana"):
        LlmConfig.model_validate(
            {**llm.model_dump(), "context_window_tokens": 1000, "response_reserve_tokens": 1000}
        )


# --- Recuperación con presupuesto -----------------------------------------------------------


CHUNKS = [Chunk(i, f"Fragmento {i}: " + "texto " * 15, 1, None) for i in range(3)]


@pytest.fixture
async def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(tmp_path / "chroma")
    texts = [c.text for c in CHUNKS]
    await store.add("d1", "norma.pdf", CHUNKS, await FakeEmbeddings().embed(texts))
    return store


def _retriever(store: ChromaVectorStore) -> Retriever:
    settings = Settings(rag_min_score=0, rag_scoped_min_score=0, rag_full_document_max_chars=10_000)
    return Retriever(FakeEmbeddings(), store, StaticConfig.from_settings(settings))


async def test_retrieval_within_a_budget(store: ChromaVectorStore) -> None:
    retriever = _retriever(store)
    one = len(CHUNKS[0].text) + SOURCE_OVERHEAD_CHARS

    # Sin presupuesto, o si cabe: el documento completo.
    assert (await retriever.retrieve_within("texto", ["d1"])).sources[0].full_document
    whole = await retriever.retrieve_within("texto", ["d1"], 100_000)
    assert whole.sources[0].full_document
    assert not whole.full_document_skipped

    # No cabe: sus fragmentos, los que quepan.
    fragments = await retriever.retrieve_within("texto", ["d1"], one + 10)
    assert fragments.full_document_skipped
    assert [s.full_document for s in fragments.sources] == [False]
    assert fragments.dropped == 2

    # Sin sitio: ninguna fuente (aunque sin presupuesto el primero entra siempre).
    empty = await retriever.retrieve_within("texto", None, 0)
    assert (empty.sources, empty.dropped) == ([], 3)
    assert len((await retriever.retrieve_within("texto")).sources) == 3


# --- Chat -----------------------------------------------------------------------------------


async def test_without_a_window_nothing_changes(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    document_id = await _index(app)
    await _ask(client, "Resúmeme las normas", document_ids=[document_id])

    [prompt] = llm.calls
    assert FULL_DOCUMENT_NOTE in prompt[0].content
    log = await _last_log(app)
    assert log.prompt_tokens_estimated == messages_tokens(prompt)
    assert log.prompt_trim is None
    assert log.prompt_tokens is None


async def test_the_whole_document_becomes_fragments_if_it_does_not_fit(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    document_id = await _index(app)
    limit = await _window(app, 900)

    with caplog.at_level(logging.WARNING):
        events = await _ask(client, "Resúmeme las normas", document_ids=[document_id])

    assert [kind for kind, _ in events][-1] == "done"
    [prompt] = llm.calls
    assert FULL_DOCUMENT_NOTE not in prompt[0].content
    assert "Fuente:" in prompt[0].content
    assert messages_tokens(prompt) <= limit
    log = await _last_log(app)
    assert log.prompt_trim is not None
    assert log.prompt_trim["full_document"] is True
    assert log.prompt_trim["sources"] >= 1
    assert "Prompt ajustado a la ventana del modelo" in caplog.text


async def test_old_history_goes_before_the_last_exchange(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    llm.chunks = ["respuesta " * 100]  # unos 1.000 caracteres por respuesta
    first = await _ask(client, "Primera pregunta")
    conversation_id = first[0][1]["id"]
    for question in ("Segunda pregunta", "Tercera pregunta"):
        await _ask(client, question, conversation_id=conversation_id)
    # Caben el prompt de sistema, la pregunta y un intercambio, pero no dos.
    limit = await _window(app, 800, reserve=200)

    await _ask(client, "Cuarta pregunta", conversation_id=conversation_id)

    prompt = llm.calls[-1]
    assert [m.role for m in prompt] == ["system", "user", "assistant", "user"]
    assert [m.content for m in prompt if m.role == "user"] == [
        "Tercera pregunta",
        "Cuarta pregunta",
    ]
    assert messages_tokens(prompt) <= limit
    assert (await _last_log(app)).prompt_trim == {"history": 4}


async def test_documents_go_before_the_last_exchange(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await _index(app)
    llm.chunks = ["respuesta " * 100]
    first = await _ask(client, "¿Cuántos días de vacaciones tengo?")
    conversation_id = first[0][1]["id"]
    # Sitio para el último intercambio y poco más: los fragmentos se quedan fuera.
    await _window(app, 900, reserve=200)

    await _ask(client, "¿Y de teletrabajo?", conversation_id=conversation_id)

    prompt = llm.calls[-1]
    assert [m.role for m in prompt] == ["system", "user", "assistant", "user"]
    log = await _last_log(app)
    assert log.prompt_trim is not None
    assert log.prompt_trim["sources"] >= 1
    assert "history" not in log.prompt_trim


async def test_a_question_that_does_not_fit_is_not_sent(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM
) -> None:
    await _window(app, 150, reserve=50)

    events = await _ask(client, "pregunta muy larga " * 30)

    assert events[-1] == ("error", {"detail": TOO_LARGE_MESSAGE})
    assert llm.calls == []
    log = await _last_log(app)
    assert log.status == "error"
    assert "El prompt mínimo" in (log.error_detail or "")


async def test_the_server_count_reveals_a_silent_trim(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    document_id = await _index(app)
    llm.prompt_tokens = 50  # el servidor «recortó» el prompt

    with caplog.at_level(logging.WARNING):
        await _ask(client, "Resúmeme las normas", document_ids=[document_id])

    log = await _last_log(app)
    assert (log.prompt_tokens, log.prompt_trim) == (50, {"server": True})
    assert "probablemente lo ha recortado" in caplog.text

    # Con un recuento parecido al estimado no se avisa.
    llm.prompt_tokens = log.prompt_tokens_estimated
    await _ask(client, "Resúmeme las normas", document_ids=[document_id])
    assert (await _last_log(app)).prompt_trim is None


async def test_the_server_count_reveals_an_underestimate(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    limit = await _window(app, 8192, reserve=1024)

    # Más que el límite, pero cabe en la ventana: ocupó parte de la reserva (#101).
    llm.prompt_tokens = limit + 300
    with caplog.at_level(logging.WARNING):
        await _ask(client, "¿Cuántos días de vacaciones tengo?")
    log = await _last_log(app)
    assert log.prompt_trim == {"underestimated": True}
    assert "La estimación se ha quedado corta" in caplog.text
    assert "probablemente lo ha recortado" not in caplog.text

    # Llenó la ventana: además, probablemente la recortó.
    caplog.clear()
    llm.prompt_tokens = 8150
    with caplog.at_level(logging.WARNING):
        await _ask(client, "¿Cuántos días de vacaciones tengo?")
    assert (await _last_log(app)).prompt_trim == {"server": True, "underestimated": True}
    assert "ha llenado la ventana de 8192" in caplog.text

    # Dentro del límite no se avisa.
    llm.prompt_tokens = limit
    await _ask(client, "¿Cuántos días de vacaciones tengo?")
    assert (await _last_log(app)).prompt_trim is None


# --- Administración -----------------------------------------------------------------------


async def test_saving_warns_if_the_whole_document_does_not_fit(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    settings.admin_password = SecretStr("secreto")
    login = {"username": "admin", "password": "secreto"}
    assert (await client.post("/api/v1/admin/session", json=login)).status_code == 200
    url = "/api/v1/admin/config"

    # 100.000 caracteres son unos 28.600 tokens: no caben en 8192 - 1024.
    saved = await client.put(f"{url}/llm", json={"context_window_tokens": 8192})
    assert saved.status_code == 200, saved.text
    [warning] = saved.json()["warnings"]
    assert "no cabe en la ventana del modelo (7168 tokens para el prompt)" in warning

    fits = await client.put(f"{url}/rag", json={"full_document_max_chars": 20_000})
    assert fits.json()["warnings"] == []
    unchecked = await client.put(f"{url}/llm", json={"context_window_tokens": 0})
    assert unchecked.json()["warnings"] == []


def test_trim_labels() -> None:
    assert trim_label(None) == ""
    assert trim_label({"full_document": True, "sources": 1, "history": 4, "server": True}) == (
        "documento completo sustituido por fragmentos; 1 fragmento; 4 mensajes del historial; "
        "recortado por el servidor"
    )
    assert trim_label({"sources": 3, "history": 1}) == "3 fragmentos; 1 mensaje del historial"
    assert trim_label({"server": True, "underestimated": True}) == (
        "recortado por el servidor; estimación por debajo del real"
    )


async def test_trims_in_the_audit_and_the_dashboard(
    app: FastAPI, client: AsyncClient, llm: ScriptedLLM, settings: Settings
) -> None:
    document_id = await _index(app)
    await _ask(client, "Sin recortes")
    await _window(app, 900)
    await _ask(client, "Resúmeme las normas", document_ids=[document_id])
    settings.admin_password = SecretStr("secreto")
    login = {"username": "admin", "password": "secreto"}
    assert (await client.post("/api/v1/admin/session", json=login)).status_code == 200

    summary = (await client.get("/api/v1/admin/dashboard/summary")).json()
    assert (summary["questions"], summary["trimmed"]) == (2, 1)

    items = (await client.get("/api/v1/admin/audit/log")).json()["items"]
    trimmed = next(e for e in items if e["question"] == "Resúmeme las normas")
    assert trimmed["prompt_trim"]["full_document"] is True
    assert trimmed["prompt_tokens_estimated"] > 0
    csv = (await client.get("/api/v1/admin/audit/log.csv")).text
    assert "Recortes del prompt" in csv
    assert "documento completo sustituido por fragmentos" in csv
