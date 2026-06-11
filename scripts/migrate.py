#!/usr/bin/env python3
"""
Wrapper para peewee-migrate atado a la configuración de Kairos.

Comandos:
    python scripts/migrate.py list                    # ver migraciones aplicadas
    python scripts/migrate.py create <name>           # nueva migración vacía
    python scripts/migrate.py up                      # aplicar pendientes
    python scripts/migrate.py up <name>               # aplicar hasta <name>
    python scripts/migrate.py down                    # revertir última
    python scripts/migrate.py rollback <name>         # revertir hasta <name>

Las migraciones viven en migrations/ y se versionan con git.

Para el bootstrap inicial (DB ya existente), se crea la primera migración
'000_baseline' que NO altera el schema — sólo registra el punto de partida.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from peewee_migrate import Router  # noqa: E402

from src.core.config import DB_PATH  # noqa: E402
from src.database.database import db  # noqa: E402

MIGRATIONS_DIR = _ROOT / "migrations"
MIGRATIONS_DIR.mkdir(exist_ok=True)


def get_router() -> Router:
    # Asegurar que el DB instance está conectado al path correcto
    if db.is_closed():
        db.connect(reuse_if_open=True)
    return Router(db, migrate_dir=str(MIGRATIONS_DIR))


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    cmd, *rest = argv
    r = get_router()

    if cmd == "list":
        applied = set(r.done)
        for name in r.todo:
            print(f"[ ] {name}")
        for name in applied:
            print(f"[x] {name}")
    elif cmd == "create":
        if not rest:
            print("ERROR: nombre requerido", file=sys.stderr)
            return 2
        r.create(rest[0], auto=False)
        print(f"Migración creada en {MIGRATIONS_DIR}")
    elif cmd == "up":
        target = rest[0] if rest else None
        r.run(target)
    elif cmd == "down":
        r.rollback()
    elif cmd == "rollback":
        if not rest:
            print("ERROR: nombre requerido", file=sys.stderr)
            return 2
        r.rollback(rest[0])
    else:
        print(f"Comando desconocido: {cmd}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    print(f"[migrate] DB = {DB_PATH}")
    sys.exit(main())
