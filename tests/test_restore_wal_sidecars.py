#!/usr/bin/env python3
"""
Tests del restore de DB con limpieza de sidecars WAL.

Bug del volumen Docker: restore_db.py sobrescribía /data/kairos.db pero dejaba
los sidecars kairos.db-wal / kairos.db-shm viejos en el volumen. Al abrir la DB,
SQLite reaplica ese WAL antiguo encima del .db restaurado → resucita datos
borrados / corrompe. El restore ahora elimina esos sidecars.
"""
from __future__ import annotations

import gzip
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _make_backup_gz(tmp_path: Path) -> Path:
    """Crea un .db.gz mínimo (snapshot consistente) con su sidecar .sha256."""
    raw = tmp_path / "snap.db"
    conn = sqlite3.connect(str(raw))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (id, v) VALUES (1, 'restored')")
    conn.commit()
    conn.close()

    gz = tmp_path / "snap.db.gz"
    with raw.open("rb") as fin, gzip.open(gz, "wb") as fout:
        fout.write(fin.read())
    raw.unlink()

    digest = hashlib.sha256(gz.read_bytes()).hexdigest()
    (tmp_path / "snap.db.gz.sha256").write_text(f"{digest}  {gz.name}\n")
    return gz


def test_restore_removes_stale_wal_sidecars(tmp_path):
    gz = _make_backup_gz(tmp_path)
    dst = tmp_path / "kairos.db"

    # Simular DB anterior + sidecars WAL viejos en el "volumen"
    dst.write_bytes(b"OLD DB CONTENT")
    wal = Path(f"{dst}-wal")
    shm = Path(f"{dst}-shm")
    wal.write_bytes(b"OLD WAL FRAMES")
    shm.write_bytes(b"OLD SHM")

    res = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "restore_db.py"),
         str(gz), "--db", str(dst)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    # Los sidecars viejos deben haber desaparecido
    assert not wal.exists(), "kairos.db-wal viejo no fue eliminado"
    assert not shm.exists(), "kairos.db-shm viejo no fue eliminado"

    # La DB restaurada debe ser legible y contener el dato esperado (no el viejo)
    conn = sqlite3.connect(str(dst))
    row = conn.execute("SELECT v FROM t WHERE id = 1").fetchone()
    conn.close()
    assert row == ("restored",)


def test_restore_dry_run_does_not_touch_dst(tmp_path):
    gz = _make_backup_gz(tmp_path)
    dst = tmp_path / "kairos.db"
    dst.write_bytes(b"UNTOUCHED")
    wal = Path(f"{dst}-wal")
    wal.write_bytes(b"OLD WAL")

    res = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "restore_db.py"),
         "--dry-run", str(gz), "--db", str(dst)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    # dry-run no modifica ni el .db ni borra el sidecar
    assert dst.read_bytes() == b"UNTOUCHED"
    assert wal.exists()
