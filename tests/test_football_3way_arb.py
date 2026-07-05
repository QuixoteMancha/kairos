"""Tests for the football 1X2 (Home/Draw/Away) 3-way arb scan
(football_3way_arb.py) — generalization of same_market_arb.py's 2-leg
same-market principle to N=3 legs, one event = 3 independent binary
Polymarket markets grouped under the same event.slug.
"""
from unittest.mock import MagicMock

from src.core.orchestration.football_3way_arb import (
    scan_football_3way_arb_fast,
    scan_football_3way_arb_deep,
    scan_kalshi_3way_football_fast,
)


def _leg(label, ask, liquidity=500.0, condition_id=None, clob_token_ids=None):
    return {
        "group_item_title": label,
        "best_ask": ask,
        "liquidity": liquidity,
        "condition_id": condition_id or f"cond-{label}",
        "clob_token_ids": clob_token_ids if clob_token_ids is not None else [f"yes-{label}", f"no-{label}"],
    }


def _group(slug="team-a-team-b-2026-07-05", title="Team A vs. Team B", legs=None):
    if legs is None:
        legs = [_leg("Team A", 0.40), _leg("Draw (Team A vs. Team B)", 0.30), _leg("Team B", 0.25)]
    return {"event_slug": slug, "event_title": title, "legs": legs}


def test_underpriced_3legs_yields_arb():
    # 0.40 + 0.30 + 0.25 = 0.95 implied -> ~5.3% gross edge, clears fee floor.
    group = _group()
    arbs = scan_football_3way_arb_fast([group])
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["tipo"] == "football_3way_arb"
    assert arb["category"] == "football_1x2"
    assert len(arb["legs"]) == 3
    assert all(leg["book"] == "polymarket" for leg in arb["legs"])
    assert arb["roi"] > 0


def test_normal_3legs_sums_above_one_yields_no_opportunity():
    group = _group(legs=[_leg("Team A", 0.40), _leg("Draw", 0.30), _leg("Team B", 0.31)])
    assert scan_football_3way_arb_fast([group]) == []


def test_thin_edge_discarded_after_fees():
    # imp = 0.997 -> tiny gross edge below MIN_ROI_PCT floor.
    group = _group(legs=[_leg("Team A", 0.500), _leg("Draw", 0.300), _leg("Team B", 0.197)])
    assert scan_football_3way_arb_fast([group]) == []


def test_low_liquidity_leg_discarded():
    # Gross edge is real, but one leg has only $50 liquidity < MIN_LEG_LIQUIDITY.
    group = _group(legs=[
        _leg("Team A", 0.40, liquidity=500.0),
        _leg("Draw", 0.30, liquidity=500.0),
        _leg("Team B", 0.25, liquidity=50.0),
    ])
    assert scan_football_3way_arb_fast([group]) == []


def test_non_three_leg_group_ignored():
    group = _group(legs=[_leg("Team A", 0.40), _leg("Draw", 0.30)])  # only 2 legs
    assert scan_football_3way_arb_fast([group]) == []


def test_deep_scan_verifies_against_real_order_book():
    group = _group()
    client = MagicMock()

    def _book(token_id):
        asks = {
            "yes-Team A": [(0.40, 100.0)],
            "yes-Draw (Team A vs. Team B)": [(0.30, 100.0)],
            "yes-Team B": [(0.25, 100.0)],
        }
        return {"asks": asks.get(token_id, []), "bids": []}

    client.get_order_book.side_effect = _book
    arbs = scan_football_3way_arb_deep([group], _client=client)
    assert len(arbs) == 1
    assert arbs[0]["roi"] > 0
    assert client.close.call_count == 0  # injected client not closed by us


def test_deep_scan_fails_closed_when_one_leg_book_is_empty():
    group = _group()
    client = MagicMock()

    def _book(token_id):
        if token_id == "yes-Draw (Team A vs. Team B)":
            return {"asks": [], "bids": []}  # empty book for this leg
        return {"asks": [(0.30, 100.0)], "bids": []}

    client.get_order_book.side_effect = _book
    assert scan_football_3way_arb_deep([group], _client=client) == []


def test_deep_scan_fails_closed_on_missing_token_id():
    group = _group(legs=[
        _leg("Team A", 0.40, clob_token_ids=[]),  # no yes_token_id
        _leg("Draw", 0.30),
        _leg("Team B", 0.25),
    ])
    client = MagicMock()
    assert scan_football_3way_arb_deep([group], _client=client) == []
    client.get_order_book.assert_not_called()


def test_deep_scan_skips_groups_that_never_passed_fast_filter():
    # No candidates passed in -> deep scan doesn't even touch the client.
    client = MagicMock()
    assert scan_football_3way_arb_deep([], _client=client) == []
    client.get_order_book.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# Kalshi 3-way football
# ──────────────────────────────────────────────────────────────────────────

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
        # much steeper than Polymarket's flat 1% estimate — a "thin" 5%
        # gross edge like the Polymarket fixtures above gets fully eaten by
        # Kalshi fees (confirmed manually: 0.40/0.30/0.25 asks yield gross
        # ~5.3% but net ROI of about -1%, correctly rejected). This uses a
        # wider edge so the net-of-fee test asserts something real.
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
    # Fee should be computed via Kalshi's convex taker-fee curve, not the
    # flat Polymarket estimate — sanity-check fee_cost_pct is in Kalshi's
    # plausible range (several percent, not ~1% flat like Polymarket).
    assert arb["fee_cost_pct"] > 5.0


def test_kalshi_thin_edge_discarded_after_convex_kalshi_fee():
    # 0.40/0.30/0.25 -> ~5.3% gross edge, which clears Polymarket's flat 1%
    # fee easily but is fully eaten (and then some) by Kalshi's convex
    # taker fee at these mid-range prices — a concrete example of why
    # per-platform fee modeling (not a shared flat estimate) matters.
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
