import logging
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import (
    EXAMPLE_ADMIN_PASSWORD,
    SESSION_SECRET_MIN_LENGTH,
    Settings,
    get_settings,
)
from app.main import create_app


def test_default_database_url_points_to_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    expected = f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}"
    assert settings.resolved_database_url == expected


def test_explicit_database_url_wins() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    assert settings.resolved_database_url == "sqlite+aiosqlite:///:memory:"


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_APP_NAME", "Otro")
    assert Settings().app_name == "Otro"


def test_tests_ignore_local_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ver conftest.py: un `.env` local (p. ej. con CHATBOT_ADMIN_PASSWORD) no debe
    # cambiar el resultado de los tests.
    (tmp_path / ".env").write_text("CHATBOT_APP_NAME=Desde .env\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert Settings().app_name == "Chatbot RAG"
    assert not [name for name in os.environ if name.upper().startswith("CHATBOT_")]


def test_invalid_environment_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging")


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


# --- Seguridad de la sesión (sin leer el .env local: valores explícitos) ---

STRONG_SECRET = "s" * SESSION_SECRET_MIN_LENGTH


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("secret", ["", "   "])
def test_empty_session_secret_is_replaced_by_a_random_one(secret: str) -> None:
    # `CHATBOT_SESSION_SECRET=` en el .env no puede dejar la cookie firmada con "".
    settings = _settings(session_secret=secret, admin_password="otra-contraseña")  # noqa: S106
    value = settings.session_secret.get_secret_value()
    assert len(value) >= SESSION_SECRET_MIN_LENGTH
    assert value != _settings(session_secret=secret).session_secret.get_secret_value()


def test_empty_session_secret_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_SESSION_SECRET", "")
    assert _settings().session_secret.get_secret_value() != ""


def test_short_session_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="al menos 16 caracteres"):
        _settings(session_secret="corta")  # noqa: S106
    assert _settings(session_secret=STRONG_SECRET).session_secret.get_secret_value() == (
        STRONG_SECRET
    )


@pytest.mark.parametrize(
    "sessions",
    [
        {"admin_password": "otra-contraseña"},
        {"auth_mode": "ldap", "ldap_user_base_dn": "ou=people,dc=x"},
    ],
)
@pytest.mark.parametrize("secret", [None, ""])
def test_production_requires_a_session_secret_when_there_are_sessions(
    sessions: dict[str, str], secret: str | None
) -> None:
    values: dict[str, Any] = {"environment": "production", **sessions}
    if secret is not None:
        values["session_secret"] = secret
    with pytest.raises(ValidationError, match="CHATBOT_SESSION_SECRET es obligatoria"):
        _settings(**values)
    values["session_secret"] = STRONG_SECRET
    assert _settings(**values).environment == "production"


def test_production_without_sessions_does_not_need_a_secret() -> None:
    assert _settings(environment="production").uses_sessions is False


def test_production_rejects_the_example_admin_password() -> None:
    with pytest.raises(ValidationError, match="valor de ejemplo"):
        _settings(
            environment="production",
            admin_password=EXAMPLE_ADMIN_PASSWORD,
            session_secret=STRONG_SECRET,
        )


def test_example_admin_password_only_warns_outside_production(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(data_dir=tmp_path, admin_password=EXAMPLE_ADMIN_PASSWORD)
    assert settings.uses_example_admin_password

    with caplog.at_level(logging.WARNING):
        create_app(settings)

    assert "valor de ejemplo" in caplog.text
