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

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scrapers import kalshi_client as _kalshi_client_module
from src.scrapers.kalshi_client import KalshiClient, normalize_market


@pytest.fixture(autouse=True)
def _clear_get_markets_cache():
    """get_markets() now caches results (TTL=90s, see kalshi_client.py —
    added 2026-07-04 to avoid repaying the ~48-69s full-pagination cost on
    every scan cycle). Without clearing this module-level cache between
    tests, a mocked call in one test can leak its cached result into the
    next test's assertions (same default cache_key), since the dict persists
    across the whole test-process lifetime, not per-test.
    """
    _kalshi_client_module._get_markets_cache.clear()
    yield
    _kalshi_client_module._get_markets_cache.clear()


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


def test_normalize_market_reads_live_dollars_schema():
    """Regression test — confirmed live 2026-07-05 against
    external-api.kalshi.com that the production /markets response no longer
    sends cents-keyed fields (yes_bid/yes_ask/no_bid/no_ask/last_price) at
    all, only dollar-string fields (yes_bid_dollars/yes_ask_dollars/etc,
    already 0-1 scaled, not cents). Without reading these, normalize_market()
    silently fell back to the hardcoded 0.5/0.5 default for every real
    Kalshi market."""
    raw = {
        "ticker": "KXUCLGAME-26JUL07BBLLEV-LEV",
        "event_ticker": "KXUCLGAME-26JUL07BBLLEV",
        "title": "Banja Luka vs Levski Sofia Winner?",
        "status": "active",
        "close_time": _close_time(24),
        "yes_bid_dollars": "0.3000",
        "yes_ask_dollars": "0.4000",
        "no_bid_dollars": "0.6000",
        "no_ask_dollars": "0.7000",
        "last_price_dollars": "0.3500",
    }
    event = normalize_market(raw)

    assert event["yes_bid"] == 0.30
    assert event["yes_ask"] == 0.40
    assert event["no_bid"] == 0.60
    assert event["no_ask"] == 0.70
    assert event["yes_price"] == 0.35
    assert event["no_price"] == 0.65
    # category is absent from the live schema entirely (not just a different
    # key) — normalize_market must not crash, and callers relying on
    # exclude_sports=True should know this filter is currently a no-op
    # against live data (see kalshi_client.py module docstring / Priya's report).
    assert event["category"] == "uncategorized"


def test_normalize_market_treats_zero_last_price_as_no_trade_yet():
    """Regression test — confirmed live 2026-07-05: Kalshi sends
    last_price_dollars="0.0000" as a literal sentinel for "no trade has
    happened yet" on brand-new markets, even when a real two-sided bid/ask
    spread already exists (e.g. a fresh KXDOTA2MAP-style market). Treating
    that 0 as a genuine last-traded price collapsed yes_price to 0.0 (and
    no_price to 1.0) despite live quotes showing real two-sided interest —
    which would silently recreate the same "imp always ~1.0, no candidate
    ever" structural bug already fixed for Polymarket (commit e9aa55b), this
    time via Kalshi's yes_price+no_price fallback path in same_market_arb.py.
    Must prefer the bid/ask midpoint instead."""
    raw = {
        "ticker": "KXDOTA2MAP-26JUL071230VPLGD-1",
        "event_ticker": "KXDOTA2MAP-26JUL071230VPLGD",
        "title": "Will Virtus.pro win map 1?",
        "status": "active",
        "close_time": _close_time(6),
        "yes_bid_dollars": "0.2600",
        "yes_ask_dollars": "0.3200",
        "no_bid_dollars": "0.6800",
        "no_ask_dollars": "0.7400",
        "last_price_dollars": "0.0000",
    }
    event = normalize_market(raw)

    assert event["yes_price"] == 0.29  # midpoint of 0.26/0.32, not 0.0
    assert event["no_price"] == 0.71


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


def test_get_markets_sends_max_close_ts_when_window_set():
    raw = {"markets": [_raw_market(ticker="ECON-1", category="Economics", close_time=_close_time(10))]}
    client = KalshiClient()
    with patch.object(client, "_get", return_value=_fake_response(raw)) as mock_get:
        client.get_markets(max_hours_to_resolution=48)

    _, kwargs = mock_get.call_args
    assert "max_close_ts" in kwargs["params"]
    # sanity: should be ~48h from now, not some stale/hardcoded value
    import time
    assert abs(kwargs["params"]["max_close_ts"] - (time.time() + 48 * 3600)) < 5


def test_get_markets_paginates_via_cursor():
    """Regression test for the original bug: get_markets() used to fetch
    exactly ONE page of `limit` markets and silently drop `cursor`, so a
    market resolving in <=48h that happened to live on page 2+ was NEVER
    seen. This simulates 3 pages via cursor chaining."""
    page1 = {
        "markets": [_raw_market(ticker="PAGE1", category="Economics", close_time=_close_time(10))],
        "cursor": "cursor-a",
    }
    page2 = {
        "markets": [_raw_market(ticker="PAGE2", category="Economics", close_time=_close_time(20))],
        "cursor": "cursor-b",
    }
    page3 = {
        "markets": [_raw_market(ticker="PAGE3", category="Economics", close_time=_close_time(30))],
        "cursor": "",  # empty cursor == no more pages
    }
    responses = [_fake_response(page1), _fake_response(page2), _fake_response(page3)]

    client = KalshiClient()
    with patch.object(client, "_get", side_effect=responses) as mock_get:
        markets = client.get_markets(max_hours_to_resolution=48)

    assert mock_get.call_count == 3
    tickers = {m["raw"]["ticker"] for m in markets}
    assert tickers == {"PAGE1", "PAGE2", "PAGE3"}

    # second call must carry cursor-a, third must carry cursor-b
    second_call_params = mock_get.call_args_list[1].kwargs["params"]
    third_call_params = mock_get.call_args_list[2].kwargs["params"]
    assert second_call_params["cursor"] == "cursor-a"
    assert third_call_params["cursor"] == "cursor-b"


def test_get_markets_stops_at_max_pages_cap():
    """Defensive cap — don't loop forever if Kalshi always returns a cursor."""
    infinite_page = {
        "markets": [_raw_market(ticker="X", category="Economics", close_time=_close_time(10))],
        "cursor": "always-more",
    }
    client = KalshiClient()
    with patch.object(client, "_get", return_value=_fake_response(infinite_page)) as mock_get:
        client.get_markets(max_hours_to_resolution=48)

    assert mock_get.call_count == client.MAX_PAGES
