"""Tests del worker de whitelist de BrightData (rescatado/adaptado
2026-07-30 del pre-rebuild `run_brightdata_ip_watcher_worker`). Mockea
`ensure_ip_whitelisted`/`get_brightdata_config` -- nunca pega a la API
real de BrightData."""

from __future__ import annotations

from unittest.mock import patch

from workers.brightdata_ip_watcher_worker import _run_once


def test_run_once_returns_false_when_no_proxy_configured():
    with patch("workers.brightdata_ip_watcher_worker.get_brightdata_config", return_value={}):
        assert _run_once() is False


def test_run_once_returns_false_when_missing_token_or_zone():
    with patch("workers.brightdata_ip_watcher_worker.get_brightdata_config",
               return_value={"proxy_url": "x", "api_token": "", "zone": "residential_proxy1"}):
        assert _run_once() is False


def test_run_once_calls_ensure_ip_whitelisted_with_config():
    config = {"proxy_url": "x", "api_token": "tok", "zone": "residential_proxy1"}
    with patch("workers.brightdata_ip_watcher_worker.get_brightdata_config", return_value=config):
        with patch("workers.brightdata_ip_watcher_worker.ensure_ip_whitelisted", return_value=True) as mock_ensure:
            result = _run_once()

    mock_ensure.assert_called_once_with("tok", "residential_proxy1", verbose=True)
    assert result is True
