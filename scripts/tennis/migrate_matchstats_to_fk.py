"""Migración: puebla la columna FK `match` (nueva, ver `MatchStats` en
`database/historical/models.py`) para las filas YA existentes de
`tennis_match_stats`, vinculándolas con su `Match`(=TennisScannedMatch)
real por `market_id` -- 2026-08-08, pedido explícito del usuario tras
revisar el consolidado de modelos hecho con Copilot ("modificar tablas
y realizar migraciones de data").

Alcance real medido: 66 filas en `tennis_match_stats` en producción
(mini-pc). La columna `match` es NULLABLE (`null=True`) a propósito --
todo el código existente (`database/historical/tennis/matchstats.py`)
sigue leyendo/escribiendo por `market_id` sin tocar la FK, así que esta
migración es aditiva pura: nunca puede romper una lectura existente,
solo completa un dato adicional para quien en el futuro quiera navegar
`MatchStats -> Match` vía relación real en vez de por `market_id`
crudo.

Fail-closed: una fila de `tennis_match_stats` cuyo `market_id` no
tenga un `Match` real correspondiente (no debería pasar -- todo
`MatchStats` se crea a partir de un partido ya trackeado -- pero nunca
se asume) se deja SIN vincular y se reporta, nunca se inventa una
relación."""

from __future__ import annotations

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Match, MatchStats


def run_migration(dry_run: bool = False) -> dict:
    init_db_historical()
    linked = already_linked = unmatched = 0
    unmatched_market_ids: list[str] = []

    with db_historical.connection_context():
        rows = list(MatchStats.select().where(MatchStats.market_id.is_null(False)))
        for row in rows:
            if row.match_id is not None:
                already_linked += 1
                continue
            match = Match.get_or_none(Match.market_id == row.market_id)
            if match is None:
                unmatched += 1
                unmatched_market_ids.append(row.market_id)
                continue
            if not dry_run:
                row.match = match
                row.save()
            linked += 1

    summary = {
        "total_rows": len(rows), "linked": linked, "already_linked": already_linked,
        "unmatched": unmatched, "unmatched_market_ids": unmatched_market_ids, "dry_run": dry_run,
    }
    print(f"[migrate_matchstats_to_fk] TERMINADO: {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import sys
    run_migration(dry_run=("--dry-run" in sys.argv))
