#!/usr/bin/env python3
"""
archive_bets.py — Mueve bets resueltas (won/lost) viejas de SQLite a Parquet.

Estrategia:
    - Operacional sigue ligero: SQLite queda sólo con bets pending o recientes.
    - Histórico vive en /data/bets_archive/bets_YYYY-MM.parquet.
    - DuckDB lo une vía la vista `bets_all` automáticamente (ver duck.py).

Uso:
    python -m scripts.archive_bets --older-than-days 90 --dry-run
    python -m scripts.archive_bets --older-than-days 90 --delete

Diseño:
    - Idempotente: si el .parquet del mes ya existe, hace MERGE (read+concat
      por bet_id, drop_duplicates) — no duplica filas.
    - Sólo archiva bets con status IN ('won','lost'). Pending nunca se toca.
    - --delete: tras escribir y verificar, borra las filas archivadas de SQLite.
      Sin --delete sólo escribe el parquet (modo seguro).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb  # type: ignore

# Permitir invocar como script suelto fuera de un paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import DB_PATH  # noqa: E402

DATA_DIR     = Path(DB_PATH).parent
ARCHIVE_DIR  = DATA_DIR / "bets_archive"


def _open() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("INSTALL sqlite")
    except duckdb.Error:
        pass
    con.execute("LOAD sqlite")
    safe = str(DB_PATH).replace("'", "''")
    con.execute(f"ATTACH '{safe}' AS op (TYPE sqlite)")
    return con


def archive(older_than_days: int, delete: bool, dry_run: bool) -> dict:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    summary: dict = {"cutoff": cutoff, "months": {}, "total_archived": 0, "total_deleted": 0}

    con = _open()
    try:
        # Buscar todos los meses con bets resueltas anteriores al cutoff.
        months_q = """
            SELECT DISTINCT substr(placed_at, 1, 7) AS yymm
            FROM op.walletbet
            WHERE status IN ('won','lost')
              AND placed_at < ?
            ORDER BY 1
        """
        months = [r[0] for r in con.execute(months_q, [cutoff]).fetchall() if r[0]]

        for ym in months:
            out_path = ARCHIVE_DIR / f"bets_{ym}.parquet"
            # Filtrar bets del mes anteriores al cutoff.
            select_sql = """
                SELECT *
                FROM op.walletbet
                WHERE status IN ('won','lost')
                  AND placed_at < ?
                  AND substr(placed_at, 1, 7) = ?
            """
            n_rows = con.execute(
                f"SELECT COUNT(*) FROM ({select_sql})",
                [cutoff, ym]
            ).fetchone()[0]
            if n_rows == 0:
                continue

            month_info = {"file": str(out_path), "new_rows": n_rows, "total_in_file": 0}

            if dry_run:
                summary["months"][ym] = month_info
                summary["total_archived"] += n_rows
                continue

            # Si el parquet ya existe → merge (full outer dedup por bet_id).
            if out_path.exists():
                safe_out = str(out_path).replace("'", "''")
                merge_sql = f"""
                    COPY (
                      SELECT * FROM read_parquet('{safe_out}')
                      UNION
                      {select_sql}
                    ) TO '{safe_out}' (FORMAT PARQUET, COMPRESSION ZSTD)
                """
                con.execute(merge_sql, [cutoff, ym])
            else:
                safe_out = str(out_path).replace("'", "''")
                con.execute(
                    f"COPY ({select_sql}) TO '{safe_out}' "
                    f"(FORMAT PARQUET, COMPRESSION ZSTD)",
                    [cutoff, ym],
                )

            # Verificación: contar filas en el archivo.
            month_info["total_in_file"] = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{safe_out}')"
            ).fetchone()[0]

            if delete and month_info["total_in_file"] >= n_rows:
                deleted = con.execute(
                    "DELETE FROM op.walletbet "
                    "WHERE status IN ('won','lost') "
                    "  AND placed_at < ? "
                    "  AND substr(placed_at, 1, 7) = ?",
                    [cutoff, ym],
                ).fetchone()
                # DuckDB devuelve rowcount via .fetchone()? — fallback explícito
                ndel = con.execute(
                    "SELECT changes()"
                ).fetchone()
                month_info["deleted_from_sqlite"] = n_rows
                summary["total_deleted"] += n_rows

            summary["months"][ym] = month_info
            summary["total_archived"] += n_rows

    finally:
        con.close()

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--older-than-days", type=int, default=90,
                    help="Sólo archivar bets resueltas con placed_at más antiguo que N días.")
    ap.add_argument("--delete", action="store_true",
                    help="Borrar de SQLite tras archivar (default: sólo escribir parquet).")
    ap.add_argument("--dry-run", action="store_true",
                    help="No escribir nada, sólo reportar qué se haría.")
    args = ap.parse_args()

    if args.dry_run and args.delete:
        print("⚠ --dry-run anula --delete (no se borra nada).")
        args.delete = False

    print(f"[archive] cutoff > {args.older_than_days} days  "
          f"delete={args.delete}  dry_run={args.dry_run}")
    summary = archive(args.older_than_days, args.delete, args.dry_run)
    print(f"[archive] meses procesados : {len(summary['months'])}")
    print(f"[archive] filas archivadas : {summary['total_archived']}")
    print(f"[archive] filas borradas   : {summary['total_deleted']}")
    for ym, info in summary["months"].items():
        print(f"  {ym}: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
