"""Worker independiente para completar biografía canónica de jugadores."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started
from scripts.tennis.fill_data_gaps import fill_player_bio

_WORKER_NAME = "tennis_player_bio_backfill_worker"
_INTERVAL_S = int(os.getenv("TENNIS_BIO_BACKFILL_INTERVAL_SECONDS", "3600"))
_BATCH_LIMIT = int(os.getenv("TENNIS_BIO_BACKFILL_LIMIT", "40"))


def _run_once() -> dict:
    result = fill_player_bio(limit=_BATCH_LIMIT)
    print(f"[tennis_player_bio_backfill] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    print(f"[tennis_player_bio_backfill] loop mode, interval={_INTERVAL_S}s, batch_limit={_BATCH_LIMIT}", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
        except Exception as e:
            mark_cycle_failed(_WORKER_NAME, e)
            print(f"[tennis_player_bio_backfill] error: {e}", flush=True)
        time.sleep(_INTERVAL_S)
