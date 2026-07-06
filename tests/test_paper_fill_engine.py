"""Tests for simulate_arb_legs_multi_platform() — multi-platform paper fill
engine (Polymarket real depth + Kalshi real depth, Manifold explicitly
unsupported)."""

from unittest.mock import MagicMock

from src.core.orchestration.paper_fill_engine import simulate_arb_legs_multi_platform


def _arb(legs):
    return {"legs": legs}


def test_two_leg_arb_polymarket_and_kalshi_no_exception():
    poly_client = MagicMock()
    poly_client.get_order_book.return_value = {
        "asks": [(0.40, 500.0), (0.42, 1000.0)],
        "bids": [],
    }
    kalshi_client = MagicMock()
    kalshi_client.get_order_book.return_value = {
        "yes": [(0.55, 200.0), (0.60, 800.0)],
        "no": [(0.44, 300.0)],
    }

    arb = _arb([
        {"side": "yes", "book": "polymarket", "odd": 2.5, "polymarket_token_id": "tok123"},
        {"side": "no", "book": "kalshi", "odd": 2.2, "external_id": "kalshi:FED-24DEC-T4.50"},
    ])

    result = simulate_arb_legs_multi_platform(
        arb, capital_usd=100.0, poly_client=poly_client, kalshi_client=kalshi_client,
    )

    assert len(result["legs"]) == 2
    poly_result, kalshi_result = result["legs"]
    assert poly_result["platform"] == "polymarket"
    assert poly_result["classification"] in ("full_fill", "depth_limited_partial", "depth_limited_none")
    assert kalshi_result["platform"] == "kalshi"
    assert kalshi_result["classification"] in ("full_fill", "depth_limited_partial", "depth_limited_none")


def test_manifold_leg_returns_unsupported_amm():
    arb = _arb([
        {"side": "yes", "book": "manifold", "odd": 1.8, "external_id": "manifold:abc"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=50.0)
    assert result["legs"][0]["classification"] == "unsupported_amm"
    assert result["legs"][0]["fill_pct"] is None


def test_empty_book_fails_closed_not_optimistic_fill():
    poly_client = MagicMock()
    poly_client.get_order_book.return_value = {"asks": [], "bids": []}
    kalshi_client = MagicMock()
    kalshi_client.get_order_book.return_value = {"yes": [], "no": []}

    arb = _arb([
        {"side": "yes", "book": "polymarket", "odd": 2.0, "polymarket_token_id": "tok999"},
        {"side": "yes", "book": "kalshi", "odd": 2.0, "external_id": "kalshi:XYZ"},
    ])
    result = simulate_arb_legs_multi_platform(
        arb, capital_usd=100.0, poly_client=poly_client, kalshi_client=kalshi_client,
    )
    for leg_result in result["legs"]:
        assert leg_result["fill_pct"] is None
        assert leg_result["classification"] == "no_data"


def test_network_error_fails_closed():
    poly_client = MagicMock()
    poly_client.get_order_book.side_effect = Exception("network down")

    arb = _arb([
        {"side": "yes", "book": "polymarket", "odd": 2.0, "polymarket_token_id": "tokErr"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, poly_client=poly_client)
    assert result["legs"][0]["fill_pct"] is None
    assert result["legs"][0]["classification"] == "no_data"


def test_kalshi_missing_ticker_fails_closed():
    arb = _arb([
        {"side": "yes", "book": "kalshi", "odd": 2.0},  # no external_id at all
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, kalshi_client=MagicMock())
    assert result["legs"][0]["fill_pct"] is None
    assert result["legs"][0]["classification"] == "no_data"
