"""Tests for scanning.py's Kalshi sport-matchup discovery
(_fetch_kalshi_sport_matchup_events) — analogous to the football 3-way
fetch, but for 2-outcome (one-market-per-competitor) tennis/baseball/
basketball/ice_hockey/american_football moneyline series, feeding the
ACTIVE paper-trading pipeline (raw_events) rather than the still-disabled
overvaluation model.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import scanning
from src.core.orchestration import paper_trading_cycle
from src.scrapers.kalshi_client import KalshiClient


@pytest.fixture(autouse=True)
def _clean_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def _raw_market(ticker, event_ticker, title, yes_ask_dollars, close_time="2026-07-20T00:00:00Z"):
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


def _mock_response(markets, cursor=""):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"markets": markets, "cursor": cursor}
    return resp


def test_builds_correct_event_shape_with_category_per_prefix():
    mlb_market = _raw_market("KXMLBGAME-E1-WSH", "KXMLBGAME-E1", "Washington vs A's Winner?", 0.55)
    nfl_market = _raw_market("KXNFLGAME-E2-SEA", "KXNFLGAME-E2", "Will Seattle win the Dallas vs Seattle Pro Football game?", 0.60)

    def _get(url, params=None, **kw):
        st = params.get("series_ticker")
        if st == "KXMLBGAME":
            return _mock_response([mlb_market])
        if st == "KXNFLGAME":
            return _mock_response([nfl_market])
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        events = scanning._fetch_kalshi_sport_matchup_events()

    baseball_events = [e for e in events if e["external_id"] == "kalshi:KXMLBGAME-E1-WSH"]
    football_events = [e for e in events if e["external_id"] == "kalshi:KXNFLGAME-E2-SEA"]

    assert len(baseball_events) == 1
    assert baseball_events[0]["category"] == "baseball"
    assert baseball_events[0]["platform"] == "kalshi"
    assert baseball_events[0]["title"] == "Washington vs A's Winner?"
    assert baseball_events[0]["yes_price"] is not None

    assert len(football_events) == 1
    assert football_events[0]["category"] == "american_football"


def test_paginates_via_cursor_within_a_prefix():
    page1_market = _raw_market("KXITFMATCH-E1-A", "KXITFMATCH-E1", "Will A win the A vs B match?", 0.50)
    page2_market = _raw_market("KXITFMATCH-E2-C", "KXITFMATCH-E2", "Will C win the C vs D match?", 0.45)

    call_count = {"KXITFMATCH": 0}

    def _get(url, params=None, **kw):
        st = params.get("series_ticker")
        if st == "KXITFMATCH":
            call_count["KXITFMATCH"] += 1
            if not params.get("cursor"):
                return _mock_response([page1_market], cursor="page2cursor")
            return _mock_response([page2_market], cursor="")
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        events = scanning._fetch_kalshi_sport_matchup_events()

    ids = {e["external_id"] for e in events}
    assert "kalshi:KXITFMATCH-E1-A" in ids
    assert "kalshi:KXITFMATCH-E2-C" in ids
    assert call_count["KXITFMATCH"] == 2


def test_one_prefix_failure_does_not_break_others():
    good_market = _raw_market("KXWNBAGAME-E1-PHX", "KXWNBAGAME-E1", "Connecticut vs Phoenix winner?", 0.48)

    def _get(url, params=None, **kw):
        st = params.get("series_ticker")
        if st == "KXATPMATCH":
            raise Exception("boom")
        if st == "KXWNBAGAME":
            return _mock_response([good_market])
        return _mock_response([])

    with patch.object(KalshiClient, "_get", side_effect=_get):
        events = scanning._fetch_kalshi_sport_matchup_events()

    assert any(e["external_id"] == "kalshi:KXWNBAGAME-E1-PHX" for e in events)


def test_never_raises_on_total_failure():
    with patch.object(KalshiClient, "__enter__", side_effect=Exception("network down")):
        events = scanning._fetch_kalshi_sport_matchup_events()
    assert events == []


def test_run_paper_trading_cycle_concatenates_both_fetches_deduped():
    generic_event = {
        "category": "prediction_market",
        "external_id": "kalshi:GENERIC-1",
        "platform": "kalshi",
        "title": "Will inflation exceed 3%?",
        "resolution_date": None,
        "yes_price": 0.4,
        "no_price": 0.6,
        "avg_volume": 100,
    }
    duplicate_sport_event = {
        "category": "baseball",
        "external_id": "kalshi:GENERIC-1",  # deliberately colliding id
        "platform": "kalshi",
        "title": "Washington vs A's Winner?",
        "resolution_date": None,
        "yes_price": 0.55,
        "no_price": 0.45,
        "avg_volume": 50,
    }
    unique_sport_event = {
        "category": "american_football",
        "external_id": "kalshi:KXNFLGAME-1",
        "platform": "kalshi",
        "title": "Will Seattle win the Dallas vs Seattle Pro Football game?",
        "resolution_date": None,
        "yes_price": 0.6,
        "no_price": 0.4,
        "avg_volume": 30,
    }

    captured_raw_events = {}

    def _fake_run_strategy1_pass(wallet, raw_events, **kw):
        captured_raw_events["events"] = list(raw_events)
        return {}

    with patch("src.core.orchestration.scanning._fetch_prediction_market_events", return_value=[generic_event]), \
         patch("src.core.orchestration.scanning._fetch_kalshi_sport_matchup_events",
               return_value=[duplicate_sport_event, unique_sport_event]), \
         patch("src.core.orchestration.scanning._scan_prediction_market_arbs", return_value=[]), \
         patch("src.core.orchestration.paper_trading_cycle._run_strategy1_pass", side_effect=_fake_run_strategy1_pass):
        paper_trading_cycle.run_paper_trading_cycle(wallet_label="test_sport_matchup_dedup")

    events = captured_raw_events["events"]
    ids = [e["external_id"] for e in events]
    assert ids.count("kalshi:GENERIC-1") == 1  # duplicate not added twice
    assert "kalshi:KXNFLGAME-1" in ids
    assert len(events) == 2
