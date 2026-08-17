"""Worker independiente para completar estadísticas históricas de tenis.

Corre en un contenedor propio para que los requests a Flashscore no esperen
bio, metadata, geocoding ni duraciones del ``tennis_data_gap_filler_worker``.
Cada ciclo primero obtiene stats faltantes y después convierte el JSON recién
guardado (y cualquier fila legacy) a columnas tipadas y filas por set.

Ambos pasos son incrementales e idempotentes. Un fallo en el fetch no impide
el tipado local, y el cursor rotativo de ``enrich_match_stats`` evita que los
partidos irresolubles bloqueen el resto del historial.
"""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule
from scripts.tennis.backfill_typed_match_stats import backfill_typed_match_stats
from scripts.tennis.enrich_match_stats import enrich_match_stats


_INTERVAL_S = int(os.getenv("TENNIS_STATS_BACKFILL_INTERVAL_SECONDS", "600"))
_FETCH_LIMIT = int(os.getenv("TENNIS_STATS_BACKFILL_LIMIT", "100"))
_TYPED_LIMIT = int(os.getenv("TENNIS_STATS_TYPED_BACKFILL_LIMIT", "500"))
_WORKER_NAME = "tennis_match_stats_backfill_worker"


def _run_once() -> dict:
    result = {}
    try:
        result["match_stats"] = enrich_match_stats(limit=_FETCH_LIMIT)
    except Exception as e:
        result["match_stats"] = {"error": f"{type(e).__name__}: {e}"}

    # Las filas existentes ya están tipadas. Evita escanear 500 registros
    # en cada ciclo vacío; solo procesa cuando el fetch guardó datos nuevos.
    if result["match_stats"].get("resolved", 0) > 0:
        try:
            result["match_stats_typed"] = backfill_typed_match_stats(limit=_TYPED_LIMIT)
        except Exception as e:
            result["match_stats_typed"] = {"error": f"{type(e).__name__}: {e}"}
    else:
        result["match_stats_typed"] = {"skipped": "no new stats"}

    print(f"[tennis_match_stats_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=90)
    print(
        f"[tennis_match_stats_backfill] loop mode, interval={_INTERVAL_S}s, "
        f"fetch_limit={_FETCH_LIMIT}, typed_limit={_TYPED_LIMIT}",
        flush=True,
    )
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_match_stats_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
