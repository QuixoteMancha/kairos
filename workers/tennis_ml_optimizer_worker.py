"""Entrena challengers de ``BetOnFavourite`` de forma periódica y segura.

El worker sólo escribe experimentos reproducibles en ``TennisMlExperiment``
y artefactos en ``/data/ml/tennis``. Nunca modifica la estrategia activa,
flags ni órdenes. Las promociones siguen siendo una revisión humana tras
verificar los folds temporales y la calidad de orientación de estadísticas.
"""

from __future__ import annotations

import os
import time

from database.db import init_db_historical
from database.historical.workerstate import (
    mark_cycle_failed,
    mark_cycle_finished,
    mark_cycle_started,
    wait_for_startup_schedule,
)
from scripts.tennis.ml_strategy_optimizer import run_optimizer


_WORKER_NAME = "tennis_ml_optimizer_worker"
_INTERVAL_S = int(os.getenv("TENNIS_ML_OPTIMIZER_INTERVAL_SECONDS", "86400"))


def _run_once() -> dict:
    report = run_optimizer()
    models = report["models"]
    result = {
        "dataset_sha256": report["dataset_sha256"],
        "sample_count": report["sample_count"],
        "training_through": report["training_through"],
        "eligible_for_review": sorted(
            name for name, item in models.items() if item.get("eligible_for_review")
        ),
        "dataset_quality": report["dataset_quality"],
    }
    print(f"[{_WORKER_NAME}] {result}", flush=True)
    return result


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER_NAME, _INTERVAL_S, jitter_max_seconds=900)
    print(f"[{_WORKER_NAME}] loop mode, interval={_INTERVAL_S}s", flush=True)
    while True:
        try:
            mark_cycle_started(_WORKER_NAME)
            mark_cycle_finished(_WORKER_NAME, _run_once())
        except Exception as exc:
            mark_cycle_failed(_WORKER_NAME, exc)
            print(f"[{_WORKER_NAME}] error: {exc}", flush=True)
        time.sleep(_INTERVAL_S)
