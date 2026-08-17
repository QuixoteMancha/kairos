"""Worker independiente para recalcular el Elo point-in-time ATP/WTA."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from scripts.tennis.compute_self_elo import run_elo_backfill
from scripts.tennis.compute_weighted_elo import run_weighted_elo_backfill

_WORKER_NAME = "tennis_self_elo_backfill_worker"
_INTERVAL_S = int(os.getenv("TENNIS_SELF_ELO_INTERVAL_SECONDS", "21600"))


def _run_once() -> dict:
    result = run_elo_backfill()
    result["weighted"] = run_weighted_elo_backfill()
    print(f"[tennis_self_elo_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    print(f"[tennis_self_elo_backfill] loop mode, interval={_INTERVAL_S}s", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_self_elo_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
