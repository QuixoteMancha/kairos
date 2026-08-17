"""Liquida decisiones shadow de BetOnFavourite; nunca coloca órdenes."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
)
from database.historical.tennis.shadow import settle_shadow_decisions


_INTERVAL_S = int(os.getenv("TENNIS_FAVOURITE_SHADOW_INTERVAL_SECONDS", "300"))
_WORKER_NAME = "tennis_favourite_shadow_worker"


if __name__ == "__main__":
    init_db_historical()
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = settle_shadow_decisions()
            mark_cycle_finished(_WORKER_NAME, result)
            print(f"[{_WORKER_NAME}] {result}", flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] error: {exc}", flush=True)
        time.sleep(_INTERVAL_S)
