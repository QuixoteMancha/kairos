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
from scripts.tennis.enrich_match_stats import enrich_match_stats


_INTERVAL_S = int(os.getenv("TENNIS_STATS_BACKFILL_INTERVAL_SECONDS", "600"))
_FETCH_LIMIT = int(os.getenv("TENNIS_STATS_BACKFILL_LIMIT", "100"))
_WORKER_NAME = "tennis_match_stats_backfill_worker"


def _run_once() -> dict:
    result = {}
    try:
        result["match_stats"] = enrich_match_stats(limit=_FETCH_LIMIT)
    except Exception as e:
        result["match_stats"] = {"error": f"{type(e).__name__}: {e}"}

    # El guardado actual tipa match y sets en la misma transacción. El
    # backfill legacy queda como script manual y no escanea 500 filas/ciclo.
    result["match_stats_typed"] = {"skipped": "typed on write"}

    print(f"[tennis_match_stats_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=90)
    print(
        f"[tennis_match_stats_backfill] loop mode, interval={_INTERVAL_S}s, "
        f"fetch_limit={_FETCH_LIMIT}",
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
