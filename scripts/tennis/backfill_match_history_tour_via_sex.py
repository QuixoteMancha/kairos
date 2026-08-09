"""Backfill de `MatchHistory.tour` vía el campo real "Sex: man"/"Sex:
woman" de TensExplorer -- 2026-08-09, pedido explícito del usuario:
reconstruido tras la migración `TennisMatchHistory` -> `MatchHistory`
(el script original de la misma sesión, `backfill_match_history_tour_
via_sex.py`, se había borrado como "ya ejecutado" en la limpieza de
scripts, pero el gap real de cobertura seguía sin cerrar: 446,008 de
446,014 filas `source="tennisexplorer"` en `MatchHistory` sin `tour`
identificado).

Agrupa por JUGADOR único (no por fila) -- una fila real puede tener a
la misma persona como player_a en un partido y player_b en otro, y un
partido comparte tour entre sus 2 jugadores por definición (no hace
falta resolver ambos lados por separado si ya se sabe de uno). Un solo
fetch por persona real, sin importar en cuántas filas aparezca.

Fail-closed: `get_tour_by_sex` devuelve None ante cualquier fallo
(slug no resuelto, fetch fallido, página no verificada, campo "Sex"
ausente) -- nunca se adivina "ATP por default"."""

from __future__ import annotations

import time
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_tour_by_sex

_PAUSE_BETWEEN_PLAYERS_S = 1.0


def _players_missing_tour() -> set[str]:
    """Nombres únicos (de cualquier lado, player_a o player_b) que
    aparecen en al menos una fila de `MatchHistory` sin `tour`."""
    init_db_historical()
    names: set[str] = set()
    rows = MatchHistory.select(MatchHistory.player_a_name, MatchHistory.player_b_name).where(
        MatchHistory.tour.is_null(True)
    )
    for r in rows:
        names.add(r.player_a_name)
        names.add(r.player_b_name)
    return names


def run_backfill(limit_players: int | None = None) -> dict[str, Any]:
    """Resuelve `tour` para todas las filas sin él, un fetch real por
    jugador único. `limit_players`: corta después de N jugadores
    procesados (para corridas parciales/pruebas) -- `None` = todos."""
    init_db_historical()
    players = sorted(_players_missing_tour())
    if limit_players is not None:
        players = players[:limit_players]

    resolved = failed = rows_updated = 0
    for i, name in enumerate(players):
        tour = get_tour_by_sex(name)
        if tour is None:
            failed += 1
        else:
            resolved += 1
            updated = (
                MatchHistory.update(tour=tour)
                .where(
                    MatchHistory.tour.is_null(True)
                    & ((MatchHistory.player_a_name == name) | (MatchHistory.player_b_name == name))
                )
                .execute()
            )
            rows_updated += updated
        if i < len(players) - 1:
            time.sleep(_PAUSE_BETWEEN_PLAYERS_S)

    return {
        "players_total": len(players), "players_resolved": resolved,
        "players_failed": failed, "rows_updated": rows_updated,
    }


if __name__ == "__main__":
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run_backfill(limit_players=limit))
