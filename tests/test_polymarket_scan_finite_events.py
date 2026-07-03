"""Tests for scan_polymarket_finite_events (generalized non-crypto scanner)."""
import re
from unittest.mock import patch

from src.core.orchestration import polymarket_scan as ps


def _market(question, hours, liquidity=5000.0, spread=0.02, condition_id="c1"):
    return {
        "id": condition_id,
        "condition_id": condition_id,
        "question": question,
        "event_title": question,
        "slug": question.lower().replace(" ", "-"),
        "yes_price": 0.6,
        "no_price": 0.4,
        "best_bid": 0.59,
        "best_ask": 0.61,
        "spread": spread,
        "liquidity": liquidity,
        "volume_24h": 1000.0,
        "day_change": 0.01,
        "hours_to_resolution": hours,
        "clob_token_ids": ["tok1"],
        "active": True,
        "closed": False,
        "accepting_orders": True,
    }


def test_scan_finite_events_filters_by_spread_and_limit():
    markets = [
        _market("Will the Fed cut rates today?", 6, condition_id="a"),
        _market("Will it rain in NYC tomorrow?", 20, spread=0.5, condition_id="b"),
        _market("Election result today?", 30, condition_id="c"),
    ]
    with patch.object(ps, "_iter_active_markets", return_value=markets):
        result = ps.scan_polymarket_finite_events(
            limit=10, min_liquidity=1000.0, max_spread=0.1, max_hours_to_resolution=48.0,
        )
    ids = {m["condition_id"] for m in result["markets"]}
    assert ids == {"a", "c"}
    assert result["market_type"] == "finite_event_binary"


def test_scan_finite_events_category_regex_filters():
    markets = [
        _market("Will BTC close above 100k?", 10, condition_id="crypto1"),
        _market("Will the election be decided today?", 10, condition_id="pol1"),
    ]
    with patch.object(ps, "_iter_active_markets", return_value=markets):
        result = ps.scan_polymarket_finite_events(
            limit=10, min_liquidity=1000.0, max_spread=1.0,
            category_regex=re.compile(r"election", re.IGNORECASE),
        )
    ids = {m["condition_id"] for m in result["markets"]}
    assert ids == {"pol1"}
