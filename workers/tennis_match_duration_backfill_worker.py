"""Worker independiente para completar duraciones históricas de tenis."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule,
)
from scripts.tennis.enrich_match_durations import enrich_match_durations


_WORKER_NAME = "tennis_match_duration_backfill_worker"
_INTERVAL_S = int(os.getenv("TENNIS_DURATION_BACKFILL_INTERVAL_SECONDS", "3600"))
_BATCH_LIMIT = int(os.getenv("TENNIS_DURATION_BACKFILL_LIMIT", "40"))


def _run_once() -> dict:
    result = enrich_match_durations(limit=_BATCH_LIMIT)
    print(f"[tennis_match_duration_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=120)
    print(
        f"[tennis_match_duration_backfill] loop mode, interval={_INTERVAL_S}s, "
        f"batch_limit={_BATCH_LIMIT}", flush=True,
    )
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_match_duration_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
