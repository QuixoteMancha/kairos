"""Worker independiente para nivel, categoría y ubicación de torneos."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from scripts.tennis.fill_tournament_metadata import (
    fill_tournament_levels, fill_tournament_locations, refine_generic_levels,
)

_WORKER_NAME = "tennis_tournament_metadata_backfill_worker"
_INTERVAL_S = int(os.getenv("TENNIS_TOURNAMENT_BACKFILL_INTERVAL_SECONDS", "3600"))
_GEOCODE_LIMIT = int(os.getenv("TENNIS_TOURNAMENT_GEOCODE_LIMIT", "25"))
_REFINE_LIMIT = int(os.getenv("TENNIS_TOURNAMENT_REFINE_LIMIT", "30"))


def _safe(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _run_once() -> dict:
    result = {
        "levels": _safe(fill_tournament_levels),
        "levels_refined": _safe(refine_generic_levels, limit=_REFINE_LIMIT),
        "locations": _safe(fill_tournament_locations, limit=_GEOCODE_LIMIT),
    }
    print(f"[tennis_tournament_metadata_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
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
