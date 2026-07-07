"""Smoke + behavior tests for run_brightdata_ip_watcher_worker (workers.py)
-- the in-process replacement for the never-installed
scripts/brightdata_ip_watcher.py cron job (real production incident,
2026-07-07: a changed residential IP silently broke every Sofascore/
Flashscore proxy call because nothing ever re-whitelisted it). No real
network calls."""
from __future__ import annotations

import inspect
from unittest.mock import patch

from src.core.orchestration import workers


def test_worker_exists_and_is_callable():
    assert hasattr(workers, "run_brightdata_ip_watcher_worker")
    assert callable(workers.run_brightdata_ip_watcher_worker)
    sig = inspect.signature(workers.run_brightdata_ip_watcher_worker)
    assert "interval_sec" in sig.parameters


def test_engine_wires_worker_in_startup_and_watchdog():
    import src.core.orchestration.engine as engine_mod
    src = inspect.getsource(engine_mod)
    assert "run_brightdata_ip_watcher_worker" in src
    assert "kairos-brightdata-ip-watcher" in src


def test_run_once_skips_gracefully_without_proxy_config():
    # Isolate _run_once by calling the module's internals via a 1-iteration
    # patch of time.sleep to raise, breaking the infinite loop after one pass.
    with patch("src.core.config.BRIGHTDATA_API_TOKEN", ""), \
         patch("src.core.config.BRIGHTDATA_PROXY", ""), \
         patch("time.sleep", side_effect=KeyboardInterrupt):
        try:
            workers.run_brightdata_ip_watcher_worker(interval_sec=1)
        except KeyboardInterrupt:
            pass  # expected -- we just wanted one pass through _run_once


def test_run_once_calls_ensure_ip_whitelisted_when_configured():
    with patch("src.core.config.BRIGHTDATA_API_TOKEN", "tok"), \
         patch("src.core.config.BRIGHTDATA_PROXY", "http://user-zone-myzone:pass@host:1"), \
         patch("src.core.config.BRIGHTDATA_ZONE", ""), \
         patch("src.core.brightdata_utils.ensure_ip_whitelisted", return_value=True) as mock_ensure, \
         patch("time.sleep", side_effect=KeyboardInterrupt):
        try:
            workers.run_brightdata_ip_watcher_worker(interval_sec=1)
        except KeyboardInterrupt:
            pass
    mock_ensure.assert_called_once()
    args, kwargs = mock_ensure.call_args
    assert args[0] == "tok"
    assert args[1] == "myzone"  # resolved from BRIGHTDATA_PROXY since BRIGHTDATA_ZONE was empty
