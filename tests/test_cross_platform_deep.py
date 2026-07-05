"""Tests for Strategy 2 (cross-platform hedge-arb) deep re-verification.

Gap found 2026-07-05: the fast-scan loop in scanning.py never re-checked a
Polymarket leg's candidate price against the real CLOB order book before
reporting the arb. deep_verify_cross_platform_arbs() closes that gap,
following the same fail-closed pattern as same_market_arb.py's
scan_same_market_arb_deep().
"""
from unittest.mock import MagicMock

from src.core.orchestration.cross_platform_deep import deep_verify_cross_platform_arbs


def _candidate(yes_odd=1.0 / 0.40, no_odd=1.0 / 0.40, yes_book="polymarket",
                no_book="kalshi", min_roi=1.0, min_net_roi=0.5):
    return {
        "tipo": "prediction_market_cross_platform_arb",
        "category": "politics",
        "fixture": "Will X happen",
        "roi": 20.0,
        "roi_gross": 20.0,
        "fee_cost_pct": 0.0,
        "_min_roi_pct": min_roi,
        "_min_net_roi_pct": min_net_roi,
        "best": {"yes": (round(yes_odd, 4), yes_book), "no": (round(no_odd, 4), no_book)},
        "all_book_odds": {},
        "legs": [
            {"side": "yes", "book": yes_book, "odd": round(yes_odd, 4),
             "external_id": "poly:abc", "polymarket_token_id": "yes-token" if yes_book == "polymarket" else None},
            {"side": "no", "book": no_book, "odd": round(no_odd, 4),
             "external_id": "kalshi:xyz", "polymarket_token_id": "no-token" if no_book == "polymarket" else None},
        ],
    }


def test_real_book_confirms_arb_survives_and_recomputes_from_verified_price():
    # Snapshot said yes=0.40 (odd=2.5); real book confirms cheap ask -> arb holds.
    candidate = _candidate(yes_odd=1.0 / 0.40, no_odd=1.0 / 0.40)
    client = MagicMock()
    client.get_order_book.side_effect = lambda token_id: (
        {"asks": [(0.40, 100.0)], "bids": []} if token_id == "yes-token" else {"asks": [], "bids": []}
    )
    out = deep_verify_cross_platform_arbs([candidate], _client=client)
    assert len(out) == 1
    assert out[0]["roi"] > 0
    assert "_min_roi_pct" not in out[0]
    assert "_min_net_roi_pct" not in out[0]
    # yes leg's odd recomputed from the verified 0.40 ask, unchanged from snapshot here
    assert out[0]["legs"][0]["odd"] == round(1.0 / 0.40, 4)


def test_real_book_invalidates_arb_worse_spread_drops_candidate():
    # Snapshot optimistically said yes ask ~0.40, but the real book's best ask
    # is actually 0.62 -> combined with no=0.40 (kalshi, trusted), imp=1.02 >= 0.999.
    candidate = _candidate(yes_odd=1.0 / 0.40, no_odd=1.0 / 0.40)
    client = MagicMock()
    client.get_order_book.side_effect = lambda token_id: (
        {"asks": [(0.62, 50.0)], "bids": []} if token_id == "yes-token" else {"asks": [], "bids": []}
    )
    out = deep_verify_cross_platform_arbs([candidate], _client=client)
    assert out == []


def test_network_error_fetching_real_book_fails_closed():
    candidate = _candidate()
    client = MagicMock()
    client.get_order_book.side_effect = RuntimeError("connection reset")
    out = deep_verify_cross_platform_arbs([candidate], _client=client)
    assert out == []


def test_empty_order_book_fails_closed():
    candidate = _candidate()
    client = MagicMock()
    client.get_order_book.return_value = {"asks": [], "bids": []}
    out = deep_verify_cross_platform_arbs([candidate], _client=client)
    assert out == []


def test_missing_token_id_fails_closed():
    candidate = _candidate()
    candidate["legs"][0]["polymarket_token_id"] = None
    client = MagicMock()
    out = deep_verify_cross_platform_arbs([candidate], _client=client)
    assert out == []
    client.get_order_book.assert_not_called()


def test_no_polymarket_leg_no_client_needed():
    # Both legs non-polymarket (e.g. kalshi vs manifold, hypothetically) —
    # nothing to verify against a real book, both legs trusted as-is.
    candidate = _candidate(yes_book="kalshi", no_book="manifold")
    out = deep_verify_cross_platform_arbs([candidate])
    assert len(out) == 1
    assert out[0]["roi"] > 0


def test_empty_input_returns_empty():
    assert deep_verify_cross_platform_arbs([]) == []
