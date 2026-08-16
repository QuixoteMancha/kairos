"""Rellena las columnas TIPADAS (y las filas por set) de las
`MatchStats` que ya están guardadas -- 2026-08-16.

SIN RED: las filas existentes ya tienen el `raw_stats_json` completo, así
que esto es puro re-parseo local. No se le pide nada a Flashscore.

Motivo: `save_match_stats*` guardaba únicamente el JSON crudo, dejando
las ~40 columnas tipadas del modelo en NULL en el 100% de las filas, y
`MatchStatsSet` vacía. El guardado ya está arreglado para las filas
NUEVAS (ver `matchstats.py`); esto repara las viejas.

Idempotente: solo toca filas cuyas columnas tipadas estén vacías y solo
crea filas de set que no existan todavía.
"""

from __future__ import annotations

import json
from typing import Any

from database.db import init_db_historical, db_historical
from database.historical.tennis.matchstats_parser import (
    set_number_from_key, typed_columns_from_stats,
)
from database.historical.tennis.models import MatchStats, MatchStatsSet


def backfill_typed_match_stats(limit: int | None = None) -> dict[str, Any]:
    """Returns: {"scanned", "updated", "set_rows_created", "skipped"}."""
    init_db_historical()

    query = MatchStats.select().where(MatchStats.raw_stats_json.is_null(False))
    if limit is not None:
        query = query.limit(limit)

    scanned = updated = set_rows = skipped = 0
    for row in query:
        scanned += 1
        try:
            stats = json.loads(row.raw_stats_json)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not isinstance(stats, dict):
            skipped += 1
            continue

        # --- columnas tipadas de la sección "match" ---
        columns = typed_columns_from_stats(stats.get("match") or {})
        changed = False
        for field, value in columns.items():
            if getattr(row, field, None) is None:
                setattr(row, field, value)
                changed = True
        if changed:
            row.save()
            updated += 1
        else:
            skipped += 1

        # --- filas por set que falten ---
        for key, section in stats.items():
            set_number = set_number_from_key(key)
            if set_number is None:
                continue
            exists = (
                MatchStatsSet
                .select()
                .where(
                    (MatchStatsSet.match_history == row.match_history_id)
                    & (MatchStatsSet.set_number == set_number)
                )
                .exists()
                if row.match_history_id is not None else False
            )
            if exists:
                continue
            try:
                fields: dict[str, Any] = {
                    "set_number": set_number,
                    "raw_stats_json": json.dumps(section),
                    "fetched_at": row.fetched_at,
                    **typed_columns_from_stats(section),
                }
                if row.match_history_id is not None:
                    fields["match_history"] = row.match_history_id
                else:
                    fields["match"] = row.match_id
                MatchStatsSet.create(**fields)
                set_rows += 1
            except Exception:
                continue

    result = {
        "scanned": scanned, "updated": updated,
        "set_rows_created": set_rows, "skipped": skipped,
    }
    print(f"[backfill_typed_match_stats] {result}", flush=True)
    return result


if __name__ == "__main__":
    backfill_typed_match_stats()
