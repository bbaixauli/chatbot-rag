from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI, Request

from app.core.auth import ANONYMOUS_USER, get_current_user
from app.core.config import Settings
from app.services.conversations import ConversationRepository, make_title


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hola", "Hola"),
        ("  varias\n  líneas\tcon espacios ", "varias líneas con espacios"),
        ("", "Nueva conversación"),
        ("x" * 60, "x" * 60),
        ("palabra " * 20, ("palabra " * 20)[:59].rstrip() + "…"),
    ],
)
def test_make_title(text: str, expected: str) -> None:
    assert make_title(text) == expected
    assert len(make_title(text)) <= 60


async def test_recent_messages_with_zero_limit(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        repo = ConversationRepository(session)
        conversation = await repo.create("u")
        assert await repo.recent_messages(conversation.id, 0) == []


async def test_current_user_is_anonymous_without_authentication(
    app: FastAPI, settings: Settings
) -> None:
    request = cast(Request, SimpleNamespace(session={}))
    async with app.state.sessionmaker() as db:
        assert await get_current_user(request, settings, db) is ANONYMOUS_USER
    assert ANONYMOUS_USER.id == "anonymous"
