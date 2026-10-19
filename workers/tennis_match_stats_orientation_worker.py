"""Worker gradual para corregir orientación A/B en stats históricas."""

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    wait_for_startup_schedule,
)
from scripts.tennis.repair_match_stats_orientation import repair_match_stats_orientation
from database.historical.tennis.models import MatchStats
from database.historical.workerstate import TennisMlExperiment
from workers.tennis_ml_optimizer_worker import _run_once_isolated


_INTERVAL_S = int(os.getenv("TENNIS_STATS_ORIENTATION_INTERVAL_SECONDS", "1800"))
_LIMIT = int(os.getenv("TENNIS_STATS_ORIENTATION_LIMIT", "25"))
_WORKER_NAME = "tennis_match_stats_orientation_worker"


def _train_when_orientation_complete() -> dict:
    pending = MatchStats.select().where(
        (MatchStats.is_usable == True)
        & MatchStats.player_a_is_flashscore_p1.is_null(True)
    ).count()
    if pending:
        return {"triggered": False, "orientation_pending": pending}
    if TennisMlExperiment.select().where(
        TennisMlExperiment.promotion_status.in_((
            "challenger_oriented",  # compatibility with pre-audit runs
            "research_proxy_pricing",
        ))
    ).exists():
        return {"triggered": False, "orientation_pending": 0, "reason": "already_trained"}
    # Keep the orientation daemon small.  The optimizer materializes the
    # complete sklearn dataset, so it must use the same child-process path
    # as the dedicated daily worker; otherwise this one-off completion run
    # keeps the allocator's large resident footprint for the daemon's life.
    report = _run_once_isolated()
    return {
        "triggered": True,
        "orientation_pending": 0,
        "dataset_sha256": report["dataset_sha256"],
        "sample_count": report["sample_count"],
    }


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=180)
    print(f"[{_WORKER_NAME}] interval={_INTERVAL_S}s limit={_LIMIT}", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = repair_match_stats_orientation(limit=_LIMIT)
            result["completion_training"] = _train_when_orientation_complete()
            mark_cycle_finished(_WORKER_NAME, result)
            print(f"[{_WORKER_NAME}] {result}", flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] error: {exc}", flush=True)
        time.sleep(_INTERVAL_S)
