"""Importa a la DB real de la mini-pc los JSON generados por
`scripts/export_local_tennis_data_for_minipc_migration.py` --
2026-08-04, migración de una vez de la investigación de esta sesión
(hecha por error contra la DB local) hacia el volumen real de
producción. Corrido DENTRO del contenedor `kairos`.

`TmlDatabaseMatch`/`TennisHistoricalOdds` son tablas NUEVAS en la
mini-pc (vacías) -- inserta todo directo, con `on_conflict_ignore` por
si acaso. `TennisMatchHistory` YA tiene filas reales en producción
(10,216 al momento de este script) -- deduplica por
(player_name, opponent_name, match_date, source) ANTES de insertar,
para no duplicar lo que ya existe."""

from __future__ import annotations

import json
from datetime import datetime

from database.db import init_db_historical
from database.historical.tennis import TmlDatabaseMatch, TennisHistoricalOdds, TennisMatchHistory

_BATCH_SIZE = 400  # margen bajo el límite de parámetros de SQLite (~999)


def _parse_dt(value: str | None):
    return datetime.fromisoformat(value) if value else None


def _bulk_insert(model, rows: list[dict]) -> int:
    inserted = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        model.insert_many(batch).on_conflict_ignore().execute()
        inserted += len(batch)
    return inserted


def import_tml_database_matches(path: str) -> int:
    with open(path) as f:
        rows = json.load(f)
    return _bulk_insert(TmlDatabaseMatch, rows)


def import_historical_odds(path: str) -> int:
    with open(path) as f:
        rows = json.load(f)
    for r in rows:
        r["fetched_at"] = _parse_dt(r["fetched_at"])
    return _bulk_insert(TennisHistoricalOdds, rows)


def import_match_history(path: str) -> dict[str, int]:
    with open(path) as f:
        rows = json.load(f)

    existing = {
        (r.player_name, r.opponent_name, r.match_date, r.source)
        for r in TennisMatchHistory.select(
            TennisMatchHistory.player_name, TennisMatchHistory.opponent_name,
            TennisMatchHistory.match_date, TennisMatchHistory.source,
        )
    }

    new_rows = []
    for r in rows:
        key = (r["player_name"], r["opponent_name"], r["match_date"], r["source"])
        if key in existing:
            continue
        existing.add(key)  # el propio export local puede tener duplicados internos
        r["fetched_at"] = _parse_dt(r["fetched_at"])
        r["weather_fetched_at"] = _parse_dt(r.get("weather_fetched_at"))
        new_rows.append(r)

    inserted = _bulk_insert(TennisMatchHistory, new_rows)
    return {"total_in_export": len(rows), "skipped_already_existed": len(rows) - len(new_rows), "inserted": inserted}


if __name__ == "__main__":
    import sys

    input_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
    init_db_historical()

    n_tml = import_tml_database_matches(f"{input_dir}/tml_database_match_export.json")
    print(f"tml_database_match: {n_tml} insertadas")

    n_odds = import_historical_odds(f"{input_dir}/tennis_historical_odds_export.json")
    print(f"tennis_historical_odds: {n_odds} insertadas")

    result_history = import_match_history(f"{input_dir}/tennis_match_history_export.json")
    print(f"tennis_match_history: {result_history}")
