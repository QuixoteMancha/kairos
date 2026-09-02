"""Recolector acotado de punto-a-punto y estadísticas live de tenis."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

from database.db import init_db_historical
from database.historical.tennis.live_tracking import persist_live_snapshots
from database.historical.tennis.matchtracking import (
    ensure_match_history_for_scanned_match, resolve_scanned_match_date,
)
from database.historical.tennis.models import (
    Match, ProbabilityTick,
)
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    startup_schedule_delay,
)
from outer_apis.scrapers.tennis.flashscore_scraper import (
    find_live_match_identity, get_match_statistics, get_point_by_point,
)
from scripts.tennis.rotating_batch import take_rotating_batch

_WORKER_NAME = "tennis_live_data_worker"
_INTERVAL_S = int(os.getenv("TENNIS_LIVE_DATA_INTERVAL_SECONDS", "300"))
_BATCH_SIZE = int(os.getenv("TENNIS_LIVE_DATA_BATCH_SIZE", "12"))


def _match_date(match: Match) -> str | None:
    # 2026-08-28: factorizado a `matchtracking.resolve_scanned_match_date`
    # -- esta función vivía duplicada acá y en `ensure_match_history_for_
    # scanned_match` con comportamiento DISTINTO (esta sí prefería el
    # ticker, la otra no), causa raíz confirmada de 108/175 partidos con
    # `tournament=None`. Un solo lugar ahora, ver docstring allá.
    return resolve_scanned_match_date(match.market_id, match.close_time)


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
    # 2026-08-25, bug real confirmado contra prod (125 partidos abiertos,
    # 44 ya resueltos a Flashscore -- de esos, 34 quedaban afuera de
    # SIEMPRE por este `order_by(detected_at.desc()).limit(_BATCH_SIZE)`
    # fijo: sin cursor, los partidos más nuevos siempre desplazan a los
    # más viejos, así estén en vivo y ya resueltos. Mismo patrón de
    # starvation ya arreglado en geocoding/tournament (2026-08-22) --
    # reusa `take_rotating_batch` para que ningún partido quede
    # bloqueado para siempre por el volumen de mercados nuevos.
    query = Match.select().where(
        Match.winner.is_null() & (Match.detected_at >= cutoff)
    )
    matches, _ = take_rotating_batch(
        query, Match.id, after_id=0, limit=_BATCH_SIZE,
        cursor_key="tennis_live_data_worker",
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

    # 2026-08-27, pedido explícito del usuario: se eliminan las 3 capas de
    # shadow-trading existentes (BetOnFavourite shadow, LimitArb monitor+
    # fill-verification, y LiveLimitArb acá mismo) -- van a construir UN
    # solo worker nuevo para probar Closing Scalp. Este worker sigue
    # recolectando y persistiendo punto-a-punto/stats en vivo (la
    # infraestructura de datos que el worker nuevo va a necesitar); solo se
    # quitó la evaluación/liquidación de LiveLimitArb que vivía acá.
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
