"""Tests for src.core.orchestration.fill_simulator (read-only, no order execution).

`simulate_arb_legs()` (Polymarket-only) was removed along with Polymarket
from this project 2026-07-09 — `simulate_limit_fill()` is the generic
walk-the-book primitive still used by paper_fill_engine.py's Kalshi
simulation."""
from src.core.orchestration.fill_simulator import simulate_limit_fill


def test_full_fill_when_depth_covers_capital():
    # asks: (price, size) ascending. 1000 shares @ 0.50 = $500 available.
    asks = [(0.50, 1000.0), (0.55, 500.0)]
    sim = simulate_limit_fill(asks, target_price=0.55, capital_usd=200.0, side="buy")
    assert sim["fill_pct"] == 100.0
    assert sim["filled_usd"] == 200.0
    assert sim["depth_exhausted"] is False
    assert sim["avg_fill_price"] == 0.50


def test_partial_fill_when_depth_limited():
    # Only $50 worth of qualifying depth (100 shares @ 0.50), requesting $200.
    asks = [(0.50, 100.0)]
    sim = simulate_limit_fill(asks, target_price=0.50, capital_usd=200.0, side="buy")
    assert sim["filled_usd"] == 50.0
    assert sim["fill_pct"] == 25.0
    assert sim["depth_exhausted"] is True


def test_no_fill_when_book_empty():
    sim = simulate_limit_fill([], target_price=0.50, capital_usd=200.0, side="buy")
    assert sim["filled_usd"] == 0.0
    assert sim["fill_pct"] == 0.0
    assert sim["avg_fill_price"] is None
    assert sim["depth_exhausted"] is True  # zero qualifying depth available < capital requested


def test_only_qualifying_levels_count():
    # target_price=0.50: level at 0.60 doesn't qualify for a buy (too expensive), shouldn't be counted.
    asks = [(0.50, 100.0), (0.60, 100.0)]
    sim = simulate_limit_fill(asks, target_price=0.50, capital_usd=1000.0, side="buy")
    assert sim["filled_usd"] == 50.0  # only the 0.50 level (100 * 0.50)
    assert sim["depth_exhausted"] is True
