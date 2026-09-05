"""Worker periódico: captura la hora real de inicio/fin de partidos de
tenis recién finalizados, vía `flashscore_scraper.get_match_timing()`
-- 2026-09-05, pedido explícito del usuario ("engancharlo a worker y
hacerlo persistir en base") tras confirmar en vivo que Flashscore expone
esos timestamps (campos `AD`/`AO` del feed diario, nunca mapeados
antes) pero solo dentro de una ventana angosta: el feed diario
(`f_2_{day}_3_en_1`) solo cubre ayer/hoy/mañana -- un partido que se
sale de esa ventana sin haberse capturado queda perdido para siempre
por este método (no hay backfill posible).

Candidatos: `tennis_scanned_match` (`Match`) YA resueltos (`winner` no
NULL) con `flashscore_match_id` conocido y `match_history` vinculado
(los 3 requisitos de `save_match_timing`), cuya fecha real (`
resolve_scanned_match_date`, NO `close_time` a secas -- mismo bug ya
documentado en otros workers de este paquete) cae dentro de los
últimos `_LOOKBACK_DAYS` días. Ese filtro por fecha evita pedirle a
Flashscore por partidos que YA sabemos fuera de ventana (barato,
sin red) -- `get_match_timing` igual fail-closed (`None`) si el feed
diario no lo tiene, pero no hace falta gastar la request.

Se salta cualquier partido que ya tenga `start_at` guardado
(`has_match_timing`) -- no tiene sentido re-pedir Flashscore por algo
que ya se resolvió. Batching: mismo `take_rotating_batch` anti-
starvation que el resto de los workers de este paquete (ver
`tennis_set_tick_worker.py` para el razonamiento del bug que evita)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from database.db import init_db_historical
from database.historical.tennis.matchtracking import resolve_scanned_match_date
from database.historical.tennis.models import Match
from database.historical.tennis.writer import has_match_timing, save_match_timing
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    startup_schedule_delay,
)
from outer_apis.scrapers.tennis.flashscore_scraper import get_match_timing
from scripts.tennis.rotating_batch import take_rotating_batch

_WORKER_NAME = "tennis_match_timing_worker"
_INTERVAL_S = int(os.getenv("TENNIS_MATCH_TIMING_INTERVAL_SECONDS", "300"))
_BATCH_SIZE = int(os.getenv("TENNIS_MATCH_TIMING_BATCH_SIZE", "40"))
# El feed diario solo cubre ayer/hoy/mañana (ver `get_match_timing`) --
# un margen algo más ancho (detected_at, no la fecha real todavía) para
# no re-filtrar dos veces la misma lógica antes de tener la fecha real.
_LOOKBACK_DAYS = 5


def _within_capture_window(match: Match) -> bool:
    """Fecha REAL del partido (ticker antes que `close_time`, ver
    `resolve_scanned_match_date`) dentro de los últimos `_LOOKBACK_DAYS`
    días. Fail-closed: `False` si no se puede resolver ninguna fecha
    (nunca se asume que está en ventana sin saberlo)."""
    real_date = resolve_scanned_match_date(match.market_id, match.close_time)
    if real_date is None:
        return False
    try:
        parsed = datetime.fromisoformat(real_date).date()
    except ValueError:
        return False
    today = datetime.now(timezone.utc).date()
    return 0 <= (today - parsed).days <= _LOOKBACK_DAYS


def run_once() -> dict[str, int]:
    init_db_historical()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS + 2)
    query = Match.select().where(
        Match.winner.is_null(False)
        & Match.flashscore_match_id.is_null(False)
        & Match.match_history.is_null(False)
        & (Match.detected_at >= cutoff)
    )
    matches, _ = take_rotating_batch(
        query, Match.id, after_id=0, limit=_BATCH_SIZE,
        cursor_key=_WORKER_NAME,
    )
    result = {
        "candidates": len(matches), "saved": 0, "already_done": 0,
        "out_of_window": 0, "not_yet_available": 0, "errors": 0,
    }
    for match in matches:
        try:
            if has_match_timing(match.match_history_id):
                result["already_done"] += 1
                continue
            if not _within_capture_window(match):
                result["out_of_window"] += 1
                continue
            timing = get_match_timing(match.flashscore_match_id)
            if timing is None:
                result["not_yet_available"] += 1
                continue
            if save_match_timing(match.match_history_id, match.flashscore_match_id, timing):
                result["saved"] += 1
        except Exception as exc:
            result["errors"] += 1
            print(f"[{_WORKER_NAME}] {match.market_id}: {exc}")

    print(f"[{_WORKER_NAME}] {result}")
    return result


if __name__ == "__main__":
    delay = startup_schedule_delay(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=60)
    if delay:
        time.sleep(delay)
    while True:
        mark_cycle_started(_WORKER_NAME)
        try:
            cycle_result = run_once()
            mark_cycle_finished(_WORKER_NAME, cycle_result)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] ciclo falló: {exc}")
        time.sleep(_INTERVAL_S)
