#!/usr/bin/env python3
"""
Backup automatizado de la DB SQLite de Kairos.

Uso (manual):
    python scripts/backup_db.py
    python scripts/backup_db.py --keep 30 --out /var/backups/kairos

Uso (cron / systemd timer): ver scripts/backup_db.service & .timer.

Características:
  * `sqlite3.Backup` API — consistencia online (no requiere parar el servidor).
  * Compresión gzip → ~5-10× más pequeño.
  * SHA-256 del backup → integridad verificable.
  * Rotación: mantiene los N más recientes (default 14).
  * Métrica: imprime tamaño, duración y hash; exit code != 0 ante error.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Hacer importable src.* desde scripts/
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from src.core.config import DB_PATH  # noqa: E402
except Exception:
    DB_PATH = str(_ROOT / "src" / "database" / "kairos.db")


DEFAULT_OUT  = _ROOT / "data" / "backups"
DEFAULT_KEEP = 14


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(db_path: str, out_dir: Path, keep: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path  = out_dir / f"kairos_{ts}.db"
    gz_path   = out_dir / f"kairos_{ts}.db.gz"

    t0 = time.time()

    # ── 1) sqlite3 .backup API (consistente, online) ─────────────────────────
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(raw_path))
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()

    # ── 2) Comprimir gzip ────────────────────────────────────────────────────
    with raw_path.open("rb") as fin, gzip.open(gz_path, "wb", compresslevel=6) as fout:
        for chunk in iter(lambda: fin.read(1 << 20), b""):
            fout.write(chunk)
    raw_path.unlink()  # quedarse solo con el .gz

    # ── 3) Hash de integridad ────────────────────────────────────────────────
    digest = _sha256(gz_path)
    (out_dir / f"{gz_path.name}.sha256").write_text(f"{digest}  {gz_path.name}\n")

    dt   = time.time() - t0
    size = gz_path.stat().st_size
    print(f"[backup] {gz_path.name}  size={size / 1024:.1f}KB  "
          f"sha256={digest[:16]}…  took={dt:.2f}s")

    # ── 4) Rotación ──────────────────────────────────────────────────────────
    all_backups = sorted(out_dir.glob("kairos_*.db.gz"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    for old in all_backups[keep:]:
        old.unlink(missing_ok=True)
        sha_sibling = out_dir / f"{old.name}.sha256"
        sha_sibling.unlink(missing_ok=True)
        print(f"[backup] rotated out: {old.name}")

    return gz_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kairos SQLite online backup")
    ap.add_argument("--db",   default=DB_PATH,         help="ruta a kairos.db")
    ap.add_argument("--out",  default=str(DEFAULT_OUT), help="directorio destino")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"backups a conservar (default {DEFAULT_KEEP})")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[backup] ERROR: DB no existe: {db_path}", file=sys.stderr)
        return 2

    try:
        backup(str(db_path), Path(args.out), args.keep)
        return 0
    except Exception as e:
        print(f"[backup] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
