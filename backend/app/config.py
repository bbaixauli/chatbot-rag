"""Consulta o restablece la configuración editable desde la administración (#42).

Uso (desde `backend/`, o con `docker compose exec backend` en Docker):

    python -m app.config show
    python -m app.config reset [--group GRUPO ...]

`reset` descarta lo guardado en la administración (todo o los grupos indicados) y lo vuelve
a copiar de las variables `CHATBOT_*`. Sirve de rescate si una configuración deja la
aplicación inservible. La API en marcha no lo ve hasta que se reinicia.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import TextIO

from app.core.config import Settings, get_settings
from app.core.runtime_config import GROUPS
from app.db.session import build_engine, build_sessionmaker
from app.services.runtime_config import ConfigStore


async def run(args: argparse.Namespace, settings: Settings, out: TextIO) -> int:
    engine = build_engine(settings.resolved_database_url)
    try:
        store = ConfigStore(build_sessionmaker(engine), settings)
        if args.command == "reset":
            groups = args.group or list(GROUPS)
            await store.reset(groups)
            print(
                f"Restablecido desde CHATBOT_*: {', '.join(groups)}. Reinicia el backend para "
                "aplicarlo.",
                file=out,
            )
        else:
            await store.load(seed=False)
        for name in GROUPS:
            group = store.current.group(name)
            info = store.info(name)
            when = info.updated_at.isoformat(timespec="seconds") if info.updated_at else "—"
            print(f"\n[{name}] guardado por {info.updated_by} ({when})", file=out)
            for field in type(group).model_fields:
                value = getattr(group, field)
                if field in group.SECRET_FIELDS:
                    shown = "(configurada)" if value is not None else "(vacía)"
                    if f"{name}.{field}" in store.unreadable_secrets:
                        shown = "(no se puede descifrar: se usa la de CHATBOT_*)"
                else:
                    shown = repr(value)
                print(f"  {field} = {shown}", file=out)
        return 0
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.config", description=(__doc__ or "").split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Muestra la configuración vigente (sin secretos)")
    reset = commands.add_parser("reset", help="Vuelve a copiar la configuración de CHATBOT_*")
    reset.add_argument(
        "--group", action="append", choices=list(GROUPS), help="Grupo (se puede repetir)"
    )
    return parser


def main(
    argv: Sequence[str] | None = None, settings: Settings | None = None, out: TextIO | None = None
) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, settings or get_settings(), out or sys.stdout))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
