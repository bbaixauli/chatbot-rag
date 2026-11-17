from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}"
    )

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    # Falla si los modelos tienen cambios sin migración.
    command.check(config)
