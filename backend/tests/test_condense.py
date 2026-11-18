from collections.abc import AsyncIterator, Sequence

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.services.condense import build_condense_messages, condense_question
from app.services.llm import ChatMessage, FakeLLMClient
from tests.conftest import ScriptedLLM, parse_sse
from tests.factories import MARKDOWN

PREVIOUS = [
    ChatMessage("user", "¿Cuántos días de vacaciones tengo?"),
    ChatMessage("assistant", "Tienes 23 días [1]. " + "Detalle. " * 200),
]


def test_condense_messages_include_conversation_and_trim_answers() -> None:
    system, user = build_condense_messages(PREVIOUS, "¿Y con cuánta antelación?")

    assert system.role == "system"
    assert system.content.startswith("Reformula la pregunta de seguimiento")
    assert "Usuario: ¿Cuántos días de vacaciones tengo?" in user.content
    assistant_line = next(line for line in user.content.splitlines() if line.startswith("Asis"))
    assert len(assistant_line) <= len("Asistente: ") + 500
    assert assistant_line.endswith("…")
    assert user.content.endswith(
        "Pregunta de seguimiento: ¿Y con cuánta antelación?\n\nPregunta independiente:"
    )


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            ["¿Con cuánta antelación ", "se piden las vacaciones?"],
            "¿Con cuánta antelación se piden las vacaciones?",
        ),
        (['"Pregunta independiente: ¿Cuándo?"'], "¿Cuándo?"),
        (["«Pregunta: ¿Dónde?»"], "¿Dónde?"),
        (["   "], "¿Y con cuánta antelación?"),
        (["x" * 1000], "¿Y con cuánta antelación?"),
    ],
)
async def test_condense_cleans_or_falls_back(reply: list[str], expected: str) -> None:
    llm = ScriptedLLM(reply)
    assert await condense_question(llm, PREVIOUS, "¿Y con cuánta antelación?") == expected
    assert len(llm.calls) == 1


async def test_condense_without_previous_questions_skips_the_model() -> None:
    llm = ScriptedLLM()
    assert await condense_question(llm, [], "Hola") == "Hola"
    assert await condense_question(llm, [ChatMessage("assistant", "x")], "Hola") == "Hola"
    assert llm.calls == []


async def test_condense_falls_back_when_the_model_fails() -> None:
    llm = ScriptedLLM(["parcial"], fail=True)
    assert await condense_question(llm, PREVIOUS, "¿Y?") == "¿Y?"


async def test_fake_llm_condenses_with_previous_questions() -> None:
    messages = build_condense_messages(PREVIOUS, "¿Y con cuánta antelación?")
    answer = "".join([part async for part in FakeLLMClient(0).stream_chat(messages)])
    assert answer == "¿Cuántos días de vacaciones tengo? ¿Y con cuánta antelación?"


class Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


class RecordingLLM(FakeLLMClient):
    def __init__(self) -> None:
        super().__init__(delay_seconds=0)
        self.systems: list[str] = []

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.systems.append(messages[0].content)
        async for part in super().stream_chat(messages):
            yield part


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM()


async def _chat(client: AsyncClient, message: str, conversation_id: str | None = None) -> str:
    response = await client.post(
        "/api/v1/chat", json={"message": message, "conversation_id": conversation_id}
    )
    assert response.status_code == 200
    new_id: str = parse_sse(response.text)[0][1]["id"]
    return new_id


async def _record_queries(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queries: list[str] = []
    original = app.state.retriever.retrieve

    async def spy(query: str):  # type: ignore[no-untyped-def]
        queries.append(query)
        return await original(query)

    monkeypatch.setattr(app.state.retriever, "retrieve", spy)
    return queries


async def test_follow_up_questions_are_condensed_before_searching(
    app: FastAPI, client: AsyncClient, llm: RecordingLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.state.documents.upload("guia.md", Bytes(MARKDOWN.encode()), "admin")
    await app.state.tasks.drain()
    queries = await _record_queries(app, monkeypatch)

    conversation_id = await _chat(client, "¿Cuántos días de vacaciones tengo?")
    await _chat(client, "¿Y con cuánta antelación?", conversation_id)

    assert queries == [
        "¿Cuántos días de vacaciones tengo?",
        "¿Cuántos días de vacaciones tengo? ¿Y con cuánta antelación?",
    ]
    condense_calls = [s for s in llm.systems if s.startswith("Reformula")]
    assert len(condense_calls) == 1


async def test_condensing_can_be_disabled(
    app: FastAPI, client: AsyncClient, llm: RecordingLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.settings.rag_condense_question = False
    await app.state.documents.upload("guia.md", Bytes(MARKDOWN.encode()), "admin")
    await app.state.tasks.drain()
    queries = await _record_queries(app, monkeypatch)

    conversation_id = await _chat(client, "Primera")
    await _chat(client, "Segunda", conversation_id)

    assert queries == ["Primera", "Segunda"]
    assert not any(s.startswith("Reformula") for s in llm.systems)


async def test_no_condensing_without_documents(
    client: AsyncClient, llm: RecordingLLM, settings: Settings
) -> None:
    assert settings.rag_condense_question is True
    conversation_id = await _chat(client, "Primera")
    await _chat(client, "Segunda", conversation_id)

    assert not any(s.startswith("Reformula") for s in llm.systems)


async def test_broken_index_counts_as_empty(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken() -> int:
        raise RuntimeError("chroma caído")

    monkeypatch.setattr(app.state.vector_store, "count", broken)
    assert await app.state.retriever.is_empty() is True
