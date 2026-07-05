"""Tests for scanning.py's Kalshi football 3-way discovery
(_fetch_kalshi_3way_football_events) — groups raw Kalshi /markets responses
by event_ticker and keeps only groups of exactly 3 sibling markets, mirroring
Polymarket's get_3way_events() convention.
"""
from unittest.mock import MagicMock, patch

from src.core.orchestration import scanning
from src.scrapers.kalshi_client import KalshiClient


def _raw_market(ticker, event_ticker, title, yes_ask_dollars, close_time="2026-07-08T00:00:00Z"):
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": title,
        "category": "Sports",
        "status": "open",
        "close_time": close_time,
        "yes_bid_dollars": str(round(yes_ask_dollars - 0.05, 4)),
        "yes_ask_dollars": str(yes_ask_dollars),
        "no_bid_dollars": "0.10",
        "no_ask_dollars": "0.20",
        "last_price_dollars": "0",
    }


def _mock_response(markets):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"markets": markets}
    return resp


def test_groups_exactly_three_sibling_markets_by_event_ticker():
    markets = [
        _raw_market("KXUCLGAME-E1-HOM", "KXUCLGAME-E1", "Home to win", 0.40),
        _raw_market("KXUCLGAME-E1-TIE", "KXUCLGAME-E1", "Draw", 0.30),
        _raw_market("KXUCLGAME-E1-AWY", "KXUCLGAME-E1", "Away to win", 0.25),
    ]

    def _get(url, params=None, **kw):
        if params.get("series_ticker") == "KXUCLGAME":
            return _mock_response(markets)
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        groups = scanning._fetch_kalshi_3way_football_events()

    ucl_groups = [g for g in groups if g["event_ticker"] == "KXUCLGAME-E1"]
    assert len(ucl_groups) == 1
    assert len(ucl_groups[0]["markets"]) == 3


def test_group_with_wrong_market_count_is_discarded():
    markets = [
        _raw_market("KXUCLGAME-E2-HOM", "KXUCLGAME-E2", "Home to win", 0.40),
        _raw_market("KXUCLGAME-E2-TIE", "KXUCLGAME-E2", "Draw", 0.30),
    ]  # only 2, missing away leg

    def _get(url, params=None, **kw):
        if params.get("series_ticker") == "KXUCLGAME":
            return _mock_response(markets)
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        groups = scanning._fetch_kalshi_3way_football_events()

    assert not any(g["event_ticker"] == "KXUCLGAME-E2" for g in groups)


def test_fetch_error_on_one_series_does_not_break_others():
    good_markets = [
        _raw_market("KXWCGAME-E3-HOM", "KXWCGAME-E3", "Home to win", 0.35),
        _raw_market("KXWCGAME-E3-TIE", "KXWCGAME-E3", "Draw", 0.30),
        _raw_market("KXWCGAME-E3-AWY", "KXWCGAME-E3", "Away to win", 0.30),
    ]

    def _get(url, params=None, **kw):
        st = params.get("series_ticker")
        if st == "KXUCLGAME":
            raise Exception("boom")
        if st == "KXWCGAME":
            return _mock_response(good_markets)
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        groups = scanning._fetch_kalshi_3way_football_events()

    assert any(g["event_ticker"] == "KXWCGAME-E3" for g in groups)


def test_wired_into_prediction_market_arb_tipos():
    assert "football_3way_arb" in scanning._PREDICTION_MARKET_ARB_TIPOS
