"""Worker: mantiene `MatchHistory`/ranking/stats actualizados a partir
de los partidos que Kalshi trackea en vivo -- 2026-08-08, pedido
explícito del usuario: "la idea del worker es actualizar el match
history del player + las stats de los matches nuevos, según aparezcan
matches en kalshi... el worker debe hacer fetch del historial de
matches en tennisexplorer".

Por cada `TennisScannedMatch` nuevo (`history_synced_at IS NULL`):
  1. Refresca ranking real (top-200 ATP+WTA vía
     `outer_apis.sports.tennis.rankings.get_top_rankings` -- ya cachea
     internamente y persiste como side-effect, no hace falta lógica
     propia acá).
  2. Para cada jugador real del partido (nombre CIERTO, viene del
     ticker de Kalshi, sin ambigüedad): re-fetchea su historial de
     TennisExplorer de los últimos 2 años (`backfill_player(...,
     years=[año actual, año anterior])` -- acotado, no los 11 años del
     backfill histórico completo) -- alimenta `TennisMatchHistory` Y,
     vía el harvesting ya wireado ahí (ver
     `wta_top200_historical_backfill.py::backfill_player`), puebla
     `MatchHistory` con identidad cierta por `match_id` real de
     TennisExplorer.
  3. Para los partidos de `MatchHistory` de esos jugadores que todavía
     no tienen stats, intenta Flashscore -- mismo mecanismo ya probado
     en producción por `tennis_match_tick_worker.py`
     (`find_match_id`/`get_match_statistics`), acotado a un batch
     pequeño por jugador por ciclo para no disparar de una todo el
     historial reciente.
  4. Marca el partido como sincronizado (idempotente -- no se
     reprocesa en el próximo ciclo).

Fail-closed en cada paso -- un jugador sin slug resoluble, sin stats
encontrables, un fetch que falla, etc. nunca tumba el ciclo completo ni
los demás partidos del batch."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisScannedMatch, MatchHistory
from database.historical.tennis.matchstats import has_match_stats_for_history, save_match_stats_for_history
from outer_apis.sports.tennis.rankings import get_top_rankings
from outer_apis.scrapers.tennis.flashscore_scraper import find_match_id, get_match_statistics
from scripts.tennis.wta_top200_historical_backfill import backfill_player

_BATCH_SIZE = int(os.getenv("TENNIS_HISTORY_SYNC_BATCH_SIZE", "10"))
_STATS_FILL_LIMIT_PER_PLAYER = int(os.getenv("TENNIS_HISTORY_SYNC_STATS_LIMIT", "5"))
_SLEEP_SECONDS = int(os.getenv("TENNIS_HISTORY_SYNC_SLEEP_S", "300"))


def _recent_years() -> list[int]:
    y = datetime.now(timezone.utc).year
    return [y, y - 1]


def fill_missing_stats_for_player(player_name: str, limit: int = _STATS_FILL_LIMIT_PER_PLAYER) -> dict:
    """Para las filas de `MatchHistory` YA JUGADAS (con score real) de
    `player_name` que todavía no tienen stats, intenta Flashscore --
    acotado a `limit` intentos por llamada (no todo el historial de una
    vez, evita disparar decenas de requests por un solo ciclo)."""
    filled = attempted = 0
    with db_historical.connection_context():
        rows = list(
            MatchHistory.select()
            .where(
                ((MatchHistory.player_a_name == player_name) | (MatchHistory.player_b_name == player_name))
                & (MatchHistory.score.is_null(False))
            )
            .order_by(MatchHistory.id.desc())
            .limit(50)
        )
    for mh in rows:
        if attempted >= limit:
            break
        if has_match_stats_for_history(mh.id):
            continue
        attempted += 1
        try:
            match_id = find_match_id(mh.player_a_name, mh.player_b_name, mh.match_date or "", mh.tournament)
            if not match_id:
                continue
            stats = get_match_statistics(match_id)
            if stats and save_match_stats_for_history(mh.id, match_id, stats):
                filled += 1
        except Exception:
            continue
    return {"attempted": attempted, "filled": filled}


def sync_match(match) -> dict:
    """Sincroniza historial+stats de AMBOS jugadores de `match` (una
    fila de `TennisScannedMatch`). Fail-closed por jugador -- un error
    en uno no impide procesar al otro."""
    years = _recent_years()
    result: dict = {"market_id": match.market_id, "players": {}, "stats": {}}
    for side, name in (("player_a", match.player_a_name), ("player_b", match.player_b_name)):
        try:
            result["players"][side] = backfill_player(name, years=years)
        except Exception as e:
            result["players"][side] = {"status": "error", "error": str(e)}
        try:
            result["stats"][side] = fill_missing_stats_for_player(name)
        except Exception as e:
            result["stats"][side] = {"status": "error", "error": str(e)}
    return result


def run_once() -> dict:
    init_db_historical()
    try:
        get_top_rankings(200, "both")
    except Exception:
        pass

    with db_historical.connection_context():
        matches = list(
            TennisScannedMatch.select()
            .where(TennisScannedMatch.history_synced_at.is_null())
            .order_by(TennisScannedMatch.detected_at.desc())
            .limit(_BATCH_SIZE)
        )

    now = datetime.now(timezone.utc)
    synced = 0
    for match in matches:
        sync_match(match)
        with db_historical.connection_context():
            match.history_synced_at = now
            match.save()
        synced += 1

    summary = {"matches_synced": synced, "candidates_found": len(matches)}
    print(f"[tennis_match_history_sync_worker] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[tennis_match_history_sync_worker] error: {e}", flush=True)
        time.sleep(_SLEEP_SECONDS)
