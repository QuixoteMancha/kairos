"""Captura mercados climáticos/forecasts para futuros challengers; sin órdenes."""
from __future__ import annotations

import json
import os
import time

from database.db import init_db_historical
from outer_apis.scrapers.weather.evidence_capture import Capture
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule

_WORKER = "weather_market_capture_worker"
_INTERVAL = max(60, int(os.getenv("WEATHER_MARKET_CAPTURE_INTERVAL_SECONDS", "900")))

def run_once() -> dict:
    init_db_historical()
    capture = Capture(start_year=int(os.getenv("WEATHER_HISTORY_START_YEAR", "2024")))
    try:
        budget = max(7, min(140, int(os.getenv("WEATHER_CAPTURE_REQUEST_BUDGET", "28"))))
        return capture.run(budget=budget)
    finally:
        capture.session.close()

if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER, _INTERVAL, jitter_max_seconds=60)
    while True:
        try:
            mark_cycle_started(_WORKER)
            report = run_once()
            if report["failed"]:
                mark_cycle_failed(_WORKER, RuntimeError(json.dumps(report)))
            else:
                mark_cycle_finished(_WORKER, report)
            print(json.dumps(report), flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER, exc); print(f"[{_WORKER}] {exc}", flush=True)
        time.sleep(_INTERVAL)
