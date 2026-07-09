"""Tests for the football 1X2 (Home/Draw/Away) 3-way arb scan
(football_3way_arb.py) — generalization of same_market_arb.py's 2-leg
same-market principle to N=3 legs, one event = 3 independent binary Kalshi
markets grouped under the same event_ticker. Polymarket was removed from
this project 2026-07-09 (unfillable limit orders / apparent bot-driven
queue-jumping on their book) — Kalshi is the only platform scanned.
"""
from src.core.orchestration.football_3way_arb import (
    scan_kalshi_3way_football_fast,
)


def _kalshi_market(ticker, event_ticker, title, yes_ask):
    return {
        "external_id": f"kalshi:{ticker}",
        "title": title,
        "event_ticker": event_ticker,
        "yes_ask": yes_ask,
    }


def _kalshi_group(event_ticker="KXUCLGAME-26JUL08MAXCSU", title="Maxline Vitebsk vs Uni Craiova", markets=None):
    if markets is None:
        # asks sum to 0.75 -> ~33% gross edge. Needed because Kalshi's
        # convex taker fee (kalshi_taker_fee_pct, peaks near price=0.5) is
        # much steeper than a flat 1% estimate would be — a "thin" 5%
        # gross edge gets fully eaten by Kalshi fees (confirmed manually:
        # 0.40/0.30/0.25 asks yield gross ~5.3% but net ROI of about -1%,
        # correctly rejected). This uses a wider edge so the net-of-fee test
        # asserts something real.
        markets = [
            _kalshi_market(f"{event_ticker}-MAX", event_ticker, "Maxline Vitebsk to win", 0.30),
            _kalshi_market(f"{event_ticker}-TIE", event_ticker, "Draw", 0.25),
            _kalshi_market(f"{event_ticker}-CSU", event_ticker, "Uni Craiova to win", 0.20),
        ]
    return {"event_ticker": event_ticker, "event_title": title, "markets": markets}


def test_kalshi_underpriced_3legs_yields_arb_with_kalshi_fee():
    group = _kalshi_group()
    arbs = scan_kalshi_3way_football_fast([group])
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["tipo"] == "football_3way_arb"
    assert all(leg["book"] == "kalshi" for leg in arb["legs"])
    assert arb["roi"] > 0
    # Fee should be computed via Kalshi's convex taker-fee curve — sanity
    # check fee_cost_pct is in Kalshi's plausible range (several percent).
    assert arb["fee_cost_pct"] > 5.0


def test_kalshi_thin_edge_discarded_after_convex_kalshi_fee():
    # 0.40/0.30/0.25 -> ~5.3% gross edge, fully eaten (and then some) by
    # Kalshi's convex taker fee at these mid-range prices.
    group = _kalshi_group(markets=[
        _kalshi_market("A", "EVT2", "Home", 0.40),
        _kalshi_market("B", "EVT2", "Draw", 0.30),
        _kalshi_market("C", "EVT2", "Away", 0.25),
    ])
    assert scan_kalshi_3way_football_fast([group]) == []


def test_kalshi_normal_3legs_sums_above_one_yields_no_opportunity():
    group = _kalshi_group(markets=[
        _kalshi_market("A", "EVT", "Home", 0.40),
        _kalshi_market("B", "EVT", "Draw", 0.30),
        _kalshi_market("C", "EVT", "Away", 0.31),
    ])
    assert scan_kalshi_3way_football_fast([group]) == []


def test_kalshi_non_three_leg_group_ignored():
    group = _kalshi_group(markets=[
        _kalshi_market("A", "EVT", "Home", 0.40),
        _kalshi_market("B", "EVT", "Draw", 0.30),
    ])
    assert scan_kalshi_3way_football_fast([group]) == []


def test_kalshi_missing_ask_leg_discarded():
    group = _kalshi_group(markets=[
        _kalshi_market("A", "EVT", "Home", None),
        _kalshi_market("B", "EVT", "Draw", 0.30),
        _kalshi_market("C", "EVT", "Away", 0.25),
    ])
    assert scan_kalshi_3way_football_fast([group]) == []
