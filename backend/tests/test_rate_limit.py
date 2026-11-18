import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.api import admin
from app.core.config import Settings
from app.core.rate_limit import _PRUNE_EVERY, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window_blocks_and_recovers() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)

    assert limiter.hit("a", 2) is None
    clock.now += 10
    assert limiter.hit("a", 2) is None
    assert limiter.hit("a", 2) == pytest.approx(50)
    # Otra clave tiene su propio contador.
    assert limiter.hit("b", 2) is None

    clock.now += 50  # caduca la primera petición
    assert limiter.hit("a", 2) is None
    assert limiter.hit("a", 2) == pytest.approx(10)


def test_zero_disables_the_limit() -> None:
    limiter = RateLimiter(Clock())
    assert all(limiter.hit("a", 0) is None for _ in range(100))
    assert len(limiter) == 0


def test_inactive_keys_are_pruned() -> None:
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.hit("antigua", 5)
    clock.now += 61
    for i in range(_PRUNE_EVERY):
        limiter.hit(f"k{i % 3}", 10_000)

    assert len(limiter) == 3


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr("secreto")
    settings.rate_limit_chat_per_minute = 2
    settings.rate_limit_login_per_minute = 2
    return settings


async def test_chat_returns_429_with_retry_after(client: AsyncClient) -> None:
    for _ in range(2):
        assert (await client.post("/api/v1/chat", json={"message": "hola"})).status_code == 200

    blocked = await client.post("/api/v1/chat", json={"message": "hola"})

    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    assert "Demasiadas peticiones" in blocked.json()["detail"]


async def test_login_is_limited_even_with_valid_credentials(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)
    for _ in range(2):
        bad = await client.post("/api/v1/admin/session", json={"username": "a", "password": "b"})
        assert bad.status_code == 401

    good = {"username": "admin", "password": "secreto"}
    assert (await client.post("/api/v1/admin/session", json=good)).status_code == 429
    # El resto de la API no se ve afectada.
    assert (await client.get("/api/v1/health")).status_code == 200


async def test_limits_are_per_client_ip(app: FastAPI, client: AsyncClient) -> None:
    limiter: RateLimiter = app.state.rate_limiter
    limiter.hit("chat:otra-ip", 2)
    limiter.hit("chat:otra-ip", 2)

    assert (await client.post("/api/v1/chat", json={"message": "hola"})).status_code == 200
