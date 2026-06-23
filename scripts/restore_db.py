#!/usr/bin/env python3
"""
Restore de un backup gzip de la DB. Valida SHA-256 antes de sobreescribir.

Uso:
    python scripts/restore_db.py data/backups/kairos_20260516_010000.db.gz
    python scripts/restore_db.py --dry-run data/backups/kairos_20260516_010000.db.gz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from src.core.config import DB_PATH
except Exception:
    DB_PATH = str(_ROOT / "src" / "database" / "kairos.db")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup", help=".db.gz a restaurar")
    ap.add_argument("--db", default=DB_PATH, help="destino (default: config.DB_PATH)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.backup)
    dst = Path(args.db)
    if not src.exists():
        print(f"ERROR: backup no existe: {src}", file=sys.stderr)
        return 2

    # Verificar hash si tenemos sidecar
    sha_file = src.with_suffix(src.suffix + ".sha256")
    if sha_file.exists():
        expected = sha_file.read_text().split()[0]
        actual   = _sha256(src)
        if expected != actual:
            print(f"ERROR: SHA-256 mismatch  expected={expected[:16]}…  got={actual[:16]}…",
                  file=sys.stderr)
            return 3
        print(f"[restore] sha256 OK  ({expected[:16]}…)")
    else:
        print("[restore] WARN: sin sidecar .sha256 — saltando verificación de integridad")

    if args.dry_run:
        print(f"[restore] dry-run: descomprimiría {src} → {dst}")
        return 0

    # Safety backup del destino actual
    if dst.exists():
        safety = dst.with_suffix(dst.suffix + ".pre_restore")
        shutil.copy2(dst, safety)
        print(f"[restore] safety copy → {safety}")

    with gzip.open(src, "rb") as fin, dst.open("wb") as fout:
        shutil.copyfileobj(fin, fout, length=1 << 20)

    # CRÍTICO (WAL + volumen Docker): eliminar los sidecars -wal/-shm que hayan
    # quedado de la DB anterior. Si no, SQLite reaplica el WAL viejo encima del
    # .db recién restaurado al abrirlo → resucita datos borrados / corrompe.
    # El backup (sqlite3.Backup API) ya es un snapshot consistente de una sola
    # pieza, así que el .db restaurado NO necesita ningún WAL acompañante.
    for _sidecar in (f"{dst}-wal", f"{dst}-shm"):
        p = Path(_sidecar)
        if p.exists():
            p.unlink()
            print(f"[restore] sidecar WAL eliminado → {p.name}")

    print(f"[restore] OK → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
