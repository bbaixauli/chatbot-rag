from pathlib import Path

from alembic import command
from alembic.config import Config

from tests.conftest import TEST_DATABASE_URL

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # Con la BD de pruebas (PostgreSQL), sobre ella: el conftest la deja vacía.
    url = TEST_DATABASE_URL or f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # configparser

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    # Falla si los modelos tienen cambios sin migración.
    command.check(config)
