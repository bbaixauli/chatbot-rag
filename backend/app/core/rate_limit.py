"""Límite de peticiones por cliente con ventana deslizante en memoria.

Basta para una única instancia del backend (la indexación en segundo plano ya lo exige).
Con varias réplicas habría que moverlo a un almacén compartido (p. ej. Redis).
"""

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import RuntimeConfigDep, SettingsDep
from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig

WINDOW_SECONDS = 60.0
# Cada cuántas comprobaciones se eliminan las claves inactivas.
_PRUNE_EVERY = 1000


class RateLimiter:
    """Cuenta peticiones por clave en el último minuto."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._checks = 0

    def hit(self, key: str, limit: int) -> float | None:
        """Registra una petición. Devuelve los segundos a esperar si se supera el límite."""
        if limit <= 0:
            return None
        now = self._clock()
        self._checks += 1
        if self._checks % _PRUNE_EVERY == 0:
            self._prune(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return hits[0] + WINDOW_SECONDS - now
        hits.append(now)
        return None

    def _prune(self, now: float) -> None:
        stale = [
            k for k, hits in self._hits.items() if not hits or hits[-1] <= now - WINDOW_SECONDS
        ]
        for key in stale:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)


def client_key(request: Request) -> str:
    """IP del cliente (uvicorn la toma de X-Forwarded-For si la envía el proxy de confianza)."""
    return request.client.host if request.client else "desconocido"


LimitSelector = Callable[[Settings, RuntimeConfig], tuple[str, int]]


def rate_limit(scope: str, limit_of: Callable[[RuntimeConfig], int]) -> Callable[..., None]:
    """Dependencia que aplica el límite `limit_of(config)` al ámbito indicado."""
    return scoped_rate_limit(lambda _settings, config: (scope, limit_of(config)))


def scoped_rate_limit(select: LimitSelector) -> Callable[..., None]:
    """Como `rate_limit`, pero el ámbito y el límite dependen de la configuración."""

    def dependency(request: Request, settings: SettingsDep, config: RuntimeConfigDep) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        scope, limit = select(settings, config)
        retry_after = limiter.hit(f"{scope}:{client_key(request)}", limit)
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiadas peticiones. Espera un momento e inténtalo de nuevo.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    return dependency


ChatRateLimit = Annotated[None, Depends(rate_limit("chat", lambda c: c.limits.chat_per_minute))]
LoginRateLimit = Annotated[None, Depends(rate_limit("login", lambda c: c.limits.login_per_minute))]


def _anonymous_session_limit(settings: Settings, config: RuntimeConfig) -> tuple[str, int]:
    # Con LDAP, entrar de forma anónima cuenta como un inicio de sesión más. Sin LDAP, cada
    # visitante crea su sesión privada al entrar: varias personas detrás de una misma IP
    # (p. ej. una oficina) superarían enseguida el límite de los inicios de sesión.
    if settings.auth_mode == "ldap":
        return "login", config.limits.login_per_minute
    return "private-session", config.limits.chat_per_minute


AnonymousSessionRateLimit = Annotated[None, Depends(scoped_rate_limit(_anonymous_session_limit))]
