"""Revisión de auditoría desde la línea de comandos (#50).

Uso (desde `backend/`, o con `docker compose exec backend`):

    python -m app.audit run

Pensado para lanzarlo desde un cron externo si se prefiere a la programación de la propia
aplicación. No se ejecuta si ya hay otra revisión en curso.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import build_engine, build_sessionmaker
from app.services.audit import AuditAlreadyRunningError, AuditDeps, run_audit
from app.services.runtime_config import ConfigStore


async def _run(settings: Settings) -> int:
    engine = build_engine(settings.resolved_database_url)
    try:
        sessionmaker = build_sessionmaker(engine)
        config = ConfigStore(sessionmaker, settings)
        await config.reload()
        deps = AuditDeps(sessionmaker, config, settings)
        try:
            run = await run_audit(deps, "cli")
        except AuditAlreadyRunningError:
            print("Ya hay una revisión en curso.", file=sys.stderr)
            return 2
        print(
            f"Revisión {run.id}: {run.status}. {run.reviewed} preguntas revisadas, "
            f"{run.alerts} avisos, {run.anonymized} registros anonimizados."
        )
        if run.error:
            print(f"Error: {run.error}", file=sys.stderr)
        return 1 if run.status == "failed" else 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.audit", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["run"], help="run: revisar las preguntas nuevas")
    parser.parse_args(argv)
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    return asyncio.run(_run(settings))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
