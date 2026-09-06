"""Worker independiente para nivel, categoría y ubicación de torneos."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule
from scripts.tennis.fill_tournament_metadata import (
    fill_tournament_country, fill_tournament_levels, fill_tournament_locations,
    refine_generic_levels,
)

_WORKER_NAME = "tennis_tournament_metadata_backfill_worker"
_INTERVAL_S = int(os.getenv("TENNIS_TOURNAMENT_BACKFILL_INTERVAL_SECONDS", "3600"))
_GEOCODE_LIMIT = int(os.getenv("TENNIS_TOURNAMENT_GEOCODE_LIMIT", "25"))
_REFINE_LIMIT = int(os.getenv("TENNIS_TOURNAMENT_REFINE_LIMIT", "30"))
# 2026-09-06, bug real: sin esto, Challenger/ITF (98.9% del catálogo)
# nunca tenía `country` antes de geocodificar -- ver docstring de
# `fill_tournament_country`.
_COUNTRY_LIMIT = int(os.getenv("TENNIS_TOURNAMENT_COUNTRY_LIMIT", "30"))


def _safe(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _run_once() -> dict:
    result = {
        "levels": _safe(fill_tournament_levels),
        "levels_refined": _safe(refine_generic_levels, limit=_REFINE_LIMIT),
        # Corre ANTES de `locations` a propósito: el país recién
        # completado acá es lo que le permite a `fill_tournament_locations`
        # usar la query segura "ciudad, país" en vez de caer a la
        # ambigua "solo ciudad".
        "country": _safe(fill_tournament_country, limit=_COUNTRY_LIMIT),
        "locations": _safe(fill_tournament_locations, limit=_GEOCODE_LIMIT),
    }
    print(f"[tennis_tournament_metadata_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=150)
    print(f"[tennis_tournament_metadata_backfill] loop mode, interval={_INTERVAL_S}s", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_tournament_metadata_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
