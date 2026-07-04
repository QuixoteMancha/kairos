"""Tests for the same-market YES/NO arb scan (same_market_arb.py).

Each event is checked only against itself — no cross-platform matching.
"""
from unittest.mock import MagicMock

from src.core.orchestration.same_market_arb import (
    scan_same_market_arb_fast,
    scan_same_market_arb_deep,
)


def _event(**overrides):
    base = {
        "category": "politics",
        "external_id": "polymarket:abc",
        "platform": "polymarket",
        "title": "Will X happen",
        "yes_price": 0.55,
        "no_price": 0.45,
        "clob_token_ids": ["yes-token", "no-token"],
    }
    base.update(overrides)
    return base


def test_underpriced_market_yields_arb():
    # 0.40 + 0.40 = 0.80 implied -> 20% gross edge, comfortably clears fees.
    ev = _event(yes_price=0.40, no_price=0.40)
    arbs = scan_same_market_arb_fast([ev])
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["tipo"] == "same_market_yes_no_arb"
    assert arb["roi"] > 0
    assert all(leg["book"] == "polymarket" for leg in arb["legs"])


def test_normal_market_sums_to_one_yields_no_opportunity():
    ev = _event(yes_price=0.55, no_price=0.45)  # sums to 1.0 exactly
    assert scan_same_market_arb_fast([ev]) == []


def test_thin_edge_discarded_after_fees():
    # imp = 0.985 -> gross roi 1.5%, but Kalshi's near-50/50 taker fee eats most of it.
    ev = _event(platform="kalshi", yes_price=0.495, no_price=0.49)
    assert scan_same_market_arb_fast([ev]) == []


def test_deep_scan_uses_real_order_book():
    ev = _event(yes_price=0.55, no_price=0.45, clob_token_ids=["yes-token", "no-token"])
    client = MagicMock()
    client.get_order_book.side_effect = lambda token_id: (
        {"asks": [(0.40, 100.0)], "bids": []} if token_id == "yes-token"
        else {"asks": [(0.40, 100.0)], "bids": []}
    )
    arbs = scan_same_market_arb_deep([ev], _client=client)
    assert len(arbs) == 1
    assert arbs[0]["roi"] > 0
    assert client.close.call_count == 0  # injected client not closed by us


def test_deep_scan_skips_non_polymarket_and_missing_token_ids():
    kalshi_ev = _event(platform="kalshi", clob_token_ids=[])
    no_tokens_ev = _event(clob_token_ids=[])
    client = MagicMock()
    assert scan_same_market_arb_deep([kalshi_ev, no_tokens_ev], _client=client) == []
    client.get_order_book.assert_not_called()


def test_deep_scan_empty_book_yields_no_opportunity():
    ev = _event()
    client = MagicMock()
    client.get_order_book.return_value = {"asks": [], "bids": []}
    assert scan_same_market_arb_deep([ev], _client=client) == []


def test_fast_scan_uses_best_bid_ask_for_polymarket_not_synthetic_complement():
    """Regression test for the 2026-07-04 bug: Polymarket's yes_price/
    no_price are always complementary (no_price == 1 - yes_price), so
    imp == 1.0 always and _build_opportunity() never returned a candidate.
    When best_bid/best_ask are present, the fast scan must use those
    instead — this is the ONLY way Strategy 3 can ever fire for Polymarket.
    """
    ev = _event(yes_price=0.55, no_price=0.45, best_bid=0.50, best_ask=0.48)
    # yes_ask=0.48, no_ask_approx = 1-0.50 = 0.50 -> imp=0.98 -> real edge
    arbs = scan_same_market_arb_fast([ev])
    assert len(arbs) == 1
    assert arbs[0]["roi"] > 0


def test_fast_scan_falls_back_to_synthetic_complement_without_best_bid_ask():
    # No best_bid/best_ask on the event -> old (never-firing) behavior for
    # Polymarket persists, documented limitation, not a crash.
    ev = _event(yes_price=0.55, no_price=0.45)
    assert scan_same_market_arb_fast([ev]) == []


def test_fast_scan_kalshi_unaffected_by_best_bid_ask_logic():
    # best_bid/best_ask special-casing only applies to platform=="polymarket".
    ev = _event(platform="kalshi", yes_price=0.40, no_price=0.40, best_bid=0.99, best_ask=0.01)
    arbs = scan_same_market_arb_fast([ev])
    assert len(arbs) == 1
    # roi computed from yes_price/no_price (0.40/0.40), NOT from best_bid/best_ask
    assert arbs[0]["best"]["yes"][0] == round(1.0 / 0.40, 4)
