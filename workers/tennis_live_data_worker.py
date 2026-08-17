"""Recolector acotado de punto-a-punto y estadísticas live de tenis."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone, timedelta

from database.db import init_db_historical
from database.historical.tennis.live_tracking import persist_live_snapshots
from database.historical.tennis.matchtracking import ensure_match_history_for_scanned_match
from database.historical.tennis.models import Match, ProbabilityTick
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    startup_schedule_delay,
)
from outer_apis.scrapers.tennis.flashscore_scraper import (
    find_live_match_identity, get_match_statistics, get_point_by_point,
)

_WORKER_NAME = "tennis_live_data_worker"
_INTERVAL_S = int(os.getenv("TENNIS_LIVE_DATA_INTERVAL_SECONDS", "300"))
_BATCH_SIZE = int(os.getenv("TENNIS_LIVE_DATA_BATCH_SIZE", "12"))


def _match_date(match: Match) -> str | None:
    # En mercados KX*MATCH, close_time puede ser la expiración de la serie
    # (p.ej. 31-AUG) y no el día real. El ticker sí codifica YYMONDD.
    ticker_date = re.search(r"-(\d{2}[A-Z]{3}\d{2})", match.market_id.upper())
    if ticker_date:
        try:
            return datetime.strptime(ticker_date.group(1), "%y%b%d").date().isoformat()
        except ValueError:
            pass
    value = (match.close_time or "")[:10]
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def _has_fresh_market_tick(match: Match) -> bool:
    tick = (ProbabilityTick.select().where(ProbabilityTick.match == match)
            .order_by(ProbabilityTick.observed_at.desc()).first())
    if tick is None:
        return False
    observed = tick.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed >= datetime.now(timezone.utc) - timedelta(minutes=10)


def run_once() -> dict[str, int]:
    init_db_historical()
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    matches = list(
        Match.select().where(
            Match.winner.is_null() & (Match.detected_at >= cutoff)
        ).order_by(Match.detected_at.desc()).limit(_BATCH_SIZE)
    )
    resolved = point_rows = stats_rows = unavailable = deferred = errors = 0
    for match in matches:
        try:
            # No scrapea partidos sin precio reciente: además de ahorrar carga,
            # garantiza que todo snapshot live quede alineado con mercado real.
            if not _has_fresh_market_tick(match):
                deferred += 1
                continue
            ensure_match_history_for_scanned_match(match)
            if not match.flashscore_match_id or match.player_a_is_flashscore_p1 is None:
                date = _match_date(match)
                identity = find_live_match_identity(
                    match.player_a_name, match.player_b_name, date,
                    tournament=match.tournament,
                ) if date else None
                if identity is None or not identity.get("match_id"):
                    unavailable += 1
                    continue
                match.flashscore_match_id = identity["match_id"]
                match.player_a_is_flashscore_p1 = identity["player_a_is_p1"]
                match.flashscore_resolved_at = datetime.now(timezone.utc)
                match.save(only=[
                    Match.flashscore_match_id, Match.player_a_is_flashscore_p1,
                    Match.flashscore_resolved_at,
                ])
                resolved += 1

            points = get_point_by_point(match.flashscore_match_id)
            stats = get_match_statistics(match.flashscore_match_id)
            if not points and not stats:
                unavailable += 1
                continue
            saved = persist_live_snapshots(match, points, stats)
            point_rows += int(saved["points"])
            stats_rows += int(saved["stats"])
        except Exception as exc:
            errors += 1
            print(f"[{_WORKER_NAME}] {match.market_id}: {exc}")
    result = {
        "open": len(matches), "resolved": resolved, "point_rows": point_rows,
        "stats_rows": stats_rows, "unavailable": unavailable,
        "deferred_no_fresh_tick": deferred, "errors": errors,
    }
    print(f"[{_WORKER_NAME}] {result}")
    return result


if __name__ == "__main__":
    delay = startup_schedule_delay(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=60)
    if delay:
        time.sleep(delay)
    while True:
        mark_cycle_started(_WORKER_NAME)
        try:
            result = run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] ciclo falló: {exc}")
        time.sleep(_INTERVAL_S)
