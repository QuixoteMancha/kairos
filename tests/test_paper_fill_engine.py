"""Tests for simulate_arb_legs_multi_platform() — paper fill engine (Kalshi
real depth; legacy AMM-style platforms explicitly unsupported). Polymarket
was removed from this project 2026-07-09 (unfillable limit orders /
apparent bot-driven queue-jumping on their book)."""

from unittest.mock import MagicMock

from src.core.orchestration.paper_fill_engine import simulate_arb_legs_multi_platform


def _arb(legs):
    return {"legs": legs}


def test_kalshi_leg_no_exception():
    kalshi_client = MagicMock()
    kalshi_client.get_order_book.return_value = {
        "yes": [(0.55, 200.0), (0.60, 800.0)],
        "no": [(0.44, 300.0)],
    }

    arb = _arb([
        {"side": "no", "book": "kalshi", "odd": 2.2, "external_id": "kalshi:FED-24DEC-T4.50"},
    ])

    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, kalshi_client=kalshi_client)

    assert len(result["legs"]) == 1
    kalshi_result = result["legs"][0]
    assert kalshi_result["platform"] == "kalshi"
    assert kalshi_result["classification"] in ("full_fill", "depth_limited_partial", "depth_limited_none")


def test_legacy_amm_leg_returns_unsupported_amm():
    arb = _arb([
        {"side": "yes", "book": "legacy_amm", "odd": 1.8, "external_id": "legacy_amm:abc"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=50.0)
    assert result["legs"][0]["classification"] == "unsupported_amm"
    assert result["legs"][0]["fill_pct"] is None


def test_empty_book_fails_closed_not_optimistic_fill():
    kalshi_client = MagicMock()
    kalshi_client.get_order_book.return_value = {"yes": [], "no": []}

    arb = _arb([
        {"side": "yes", "book": "kalshi", "odd": 2.0, "external_id": "kalshi:XYZ"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, kalshi_client=kalshi_client)
    for leg_result in result["legs"]:
        assert leg_result["fill_pct"] is None
        assert leg_result["classification"] == "no_data"


def test_network_error_fails_closed():
    kalshi_client = MagicMock()
    kalshi_client.get_order_book.side_effect = Exception("network down")

    arb = _arb([
        {"side": "yes", "book": "kalshi", "odd": 2.0, "external_id": "kalshi:ERR"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, kalshi_client=kalshi_client)
    assert result["legs"][0]["fill_pct"] is None
    assert result["legs"][0]["classification"] == "no_data"


def test_kalshi_missing_ticker_fails_closed():
    arb = _arb([
        {"side": "yes", "book": "kalshi", "odd": 2.0},  # no external_id at all
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0, kalshi_client=MagicMock())
    assert result["legs"][0]["fill_pct"] is None
    assert result["legs"][0]["classification"] == "no_data"


def test_unrecognized_platform_falls_through_no_data():
    # A leg carrying a legacy "polymarket" book (e.g. stale historical data)
    # is no longer specially handled — it fails closed like any unknown
    # platform, since PolymarketClient no longer exists in this project.
    arb = _arb([
        {"side": "yes", "book": "polymarket", "odd": 2.0, "external_id": "polymarket:legacy"},
    ])
    result = simulate_arb_legs_multi_platform(arb, capital_usd=100.0)
    assert result["legs"][0]["fill_pct"] is None
    assert result["legs"][0]["classification"] == "no_data"
