"""Tests for the same-market YES/NO arb scan (same_market_arb.py).

Each event is checked only against itself — no cross-platform matching.
Polymarket was removed from this project 2026-07-09 (unfillable limit
orders / apparent bot-driven queue-jumping on their book) — Kalshi is the
only real-money platform scanned.
"""
from src.core.orchestration.same_market_arb import scan_same_market_arb_fast


def _event(**overrides):
    base = {
        "category": "politics",
        "external_id": "kalshi:abc",
        "platform": "kalshi",
        "title": "Will X happen",
        "yes_price": 0.55,
        "no_price": 0.45,
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
    assert all(leg["book"] == "kalshi" for leg in arb["legs"])


def test_normal_market_sums_to_one_yields_no_opportunity():
    ev = _event(yes_price=0.55, no_price=0.45)  # sums to 1.0 exactly
    assert scan_same_market_arb_fast([ev]) == []


def test_thin_edge_discarded_after_fees():
    # imp = 0.985 -> gross roi 1.5%, but Kalshi's near-50/50 taker fee eats most of it.
    ev = _event(yes_price=0.495, no_price=0.49)
    assert scan_same_market_arb_fast([ev]) == []


def test_non_real_money_platform_discarded():
    ev = _event(platform="legacy_virtual")
    assert scan_same_market_arb_fast([ev]) == []
