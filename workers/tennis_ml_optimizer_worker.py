"""Worker offline champion/challenger para investigación de tenis.

No importa `engine`, estrategias ni APIs de trading. Un challenger elegible
solo queda marcado para revisión manual.
"""

from __future__ import annotations

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    TennisMlExperiment, mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    wait_for_startup_schedule,
)
from scripts.tennis.ml_strategy_optimizer import (
    collect_dataset, dataset_sha256, persist_report, train_and_evaluate,
)


_WORKER_NAME = "tennis_ml_optimizer_worker"
_INTERVAL_S = int(os.getenv("TENNIS_ML_INTERVAL_SECONDS", "86400"))
_ARTIFACT_DIR = os.getenv("TENNIS_ML_ARTIFACT_DIR", "/data/ml/tennis")


def _run_once() -> dict:
    rows = collect_dataset()
    current_hash = dataset_sha256(rows)
    latest = (
        TennisMlExperiment.select()
        .order_by(TennisMlExperiment.created_at.desc())
        .first()
    )
    if latest is not None and latest.dataset_sha256 == current_hash:
        return {
            "skipped": "dataset unchanged",
            "dataset_sha256": current_hash,
            "sample_count": len(rows),
        }
    preview = train_and_evaluate(rows, _ARTIFACT_DIR)
    persist_report(preview)
    return preview


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=300)
    print(
        f"[tennis_ml_optimizer] loop mode, interval={_INTERVAL_S}s, "
        f"artifact_dir={_ARTIFACT_DIR}, promotion=manual_only",
        flush=True,
    )
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            result = _run_once()
            mark_cycle_finished(_WORKER_NAME, result)
            print(f"[tennis_ml_optimizer] {result}", flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[tennis_ml_optimizer] error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(_INTERVAL_S)
