"""Worker gradual para corregir orientación A/B en stats históricas."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    wait_for_startup_schedule,
)
from scripts.tennis.repair_match_stats_orientation import repair_match_stats_orientation


_INTERVAL_S = int(os.getenv("TENNIS_STATS_ORIENTATION_INTERVAL_SECONDS", "1800"))
_LIMIT = int(os.getenv("TENNIS_STATS_ORIENTATION_LIMIT", "25"))
_WORKER_NAME = "tennis_match_stats_orientation_worker"


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=180)
    print(f"[{_WORKER_NAME}] interval={_INTERVAL_S}s limit={_LIMIT}", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = repair_match_stats_orientation(limit=_LIMIT)
            mark_cycle_finished(_WORKER_NAME, result)
            print(f"[{_WORKER_NAME}] {result}", flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] error: {exc}", flush=True)
        time.sleep(_INTERVAL_S)
