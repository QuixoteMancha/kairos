"""Tests for the football 1X2 (Home/Draw/Away) 3-way arb scan
(football_3way_arb.py) — generalization of same_market_arb.py's 2-leg
same-market principle to N=3 legs, one event = 3 independent binary
Polymarket markets grouped under the same event.slug.
"""
from unittest.mock import MagicMock

from src.core.orchestration.football_3way_arb import (
    scan_football_3way_arb_fast,
    scan_football_3way_arb_deep,
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
