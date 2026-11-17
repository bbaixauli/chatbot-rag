from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


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


def test_invalid_environment_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging")


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
