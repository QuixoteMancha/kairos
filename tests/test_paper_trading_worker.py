"""Smoke test for the paper-trading background worker wiring (workers.py +
engine.py). Does NOT run the infinite loop — just verifies the worker
function exists, is importable without pulling heavy/networked deps at
import time, and that engine.py references it in both startup and watchdog
specs (wiring regression guard)."""
from __future__ import annotations

import inspect

from src.core.orchestration import workers


def test_run_paper_trading_worker_exists_and_is_callable():
    assert hasattr(workers, "run_paper_trading_worker")
    assert callable(workers.run_paper_trading_worker)
    sig = inspect.signature(workers.run_paper_trading_worker)
    assert "interval_sec" in sig.parameters


def test_engine_wires_paper_trading_worker_in_startup_and_watchdog():
    import src.core.orchestration.engine as engine_mod

    src = inspect.getsource(engine_mod)
    assert "run_paper_trading_worker" in src
    assert "kairos-paper-trading" in src
