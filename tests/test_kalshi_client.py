"""
test_kalshi_client.py — normalization and finite-event filtering for the
Kalshi client (prediction-markets pivot, Phase 2).

Covers:
  - normalize_market() maps Kalshi's cents-based prices to 0-1 probabilities
    and produces the generic Event fields (platform, category, external_id).
  - get_markets() excludes sports categories by default and filters to
    events resolving within the ≤48h window (business requirement: only
    finite events resolving in ≤2 days).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scrapers.kalshi_client import KalshiClient, normalize_market


def _close_time(hours_from_now: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")


def _raw_market(**overrides) -> dict:
    base = {
        "ticker": "FED-24DEC-T4.50",
        "event_ticker": "FED-24DEC",
        "title": "Fed raises rates in December?",
        "category": "Economics",
        "status": "open",
        "close_time": _close_time(24),
        "yes_bid": 60,
        "yes_ask": 64,
        "no_bid": 36,
        "no_ask": 40,
        "last_price": 62,
    }
    base.update(overrides)
    return base


def test_normalize_market_converts_cents_to_probability():
    event = normalize_market(_raw_market())

    assert event["platform"] == "kalshi"
    assert event["external_id"] == "kalshi:FED-24DEC-T4.50"
    assert event["category"] == "economics"
    assert event["yes_price"] == 0.62
    assert event["no_price"] == 0.38
    assert event["yes_bid"] == 0.6
    assert event["yes_ask"] == 0.64
    assert 23.9 <= event["hours_to_resolution"] <= 24.1


def test_normalize_market_falls_back_to_bid_ask_midpoint_without_last_price():
    event = normalize_market(_raw_market(last_price=None))

    assert event["yes_price"] == 0.62  # midpoint of 0.60/0.64
    assert event["no_price"] == 0.38


def test_normalize_market_handles_missing_close_time():
    event = normalize_market(_raw_market(close_time=None))

    assert event["resolution_date"] is None
    assert event["hours_to_resolution"] is None


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_markets_excludes_sports_by_default():
    raw = {
        "markets": [
            _raw_market(ticker="ECON-1", category="Economics", close_time=_close_time(10)),
            _raw_market(ticker="SPORT-1", category="Sports", close_time=_close_time(10)),
        ]
    }
    client = KalshiClient()
    with patch.object(client, "_get", return_value=_fake_response(raw)) as mock_get:
        markets = client.get_markets()

    assert mock_get.called
    tickers = {m["raw"]["ticker"] for m in markets}
    assert tickers == {"ECON-1"}


def test_get_markets_filters_to_48h_resolution_window():
    raw = {
        "markets": [
            _raw_market(ticker="SOON", category="Economics", close_time=_close_time(12)),
            _raw_market(ticker="FAR", category="Economics", close_time=_close_time(24 * 10)),
        ]
    }
    client = KalshiClient()
    with patch.object(client, "_get", return_value=_fake_response(raw)):
        markets = client.get_markets(max_hours_to_resolution=48)

    tickers = {m["raw"]["ticker"] for m in markets}
    assert tickers == {"SOON"}


def test_get_markets_can_disable_filters():
    raw = {
        "markets": [
            _raw_market(ticker="SPORT-1", category="Sports", close_time=_close_time(24 * 30)),
        ]
    }
    client = KalshiClient()
    with patch.object(client, "_get", return_value=_fake_response(raw)):
        markets = client.get_markets(max_hours_to_resolution=None, exclude_sports=False)

    assert len(markets) == 1
