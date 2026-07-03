"""Tests for the prediction-markets cross-platform arb scan
(_scan_prediction_market_arbs / _fetch_prediction_market_events in scanning.py).

Uses a synthetic event that "appears" on 2-3 platforms with different prices,
mocking each platform's fetch function so no network calls happen.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.core.orchestration import scanning


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_no_cross_platform_match_yields_no_opportunity():
    single = [{
        "category": "politics", "external_id": "polymarket:abc", "platform": "polymarket",
        "title": "Will X happen", "resolution_date": _iso(datetime.now(timezone.utc) + timedelta(hours=10)),
        "yes_price": 0.6, "no_price": 0.4,
    }]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=single):
        arbs = scanning._scan_prediction_market_arbs()
    assert arbs == []


def test_cross_platform_arb_detected_with_price_divergence():
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=12)
    events = [
        {
            "category": "politics", "external_id": "polymarket:same-event", "platform": "polymarket",
            "title": "Will candidate A win the special election",
            "resolution_date": _iso(resolution), "yes_price": 0.55, "no_price": 0.45,
        },
        {
            "category": "politics", "external_id": "kalshi:SAME-EVENT", "platform": "kalshi",
            "title": "Will candidate A win the special election?",
            "resolution_date": _iso(resolution + timedelta(minutes=5)), "yes_price": 0.40, "no_price": 0.60,
        },
        {
            "category": "politics", "external_id": "manifold:999", "platform": "manifold",
            "title": "Candidate A wins special election",
            "resolution_date": _iso(resolution - timedelta(minutes=10)), "yes_price": 0.50, "no_price": 0.50,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()

    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["tipo"] == "prediction_market_cross_platform_arb"
    assert arb["category"] == "politics"
    # Best YES odds come from kalshi (yes_price=0.40 -> odds 2.5), best NO from
    # polymarket (no_price=0.45 -> odds ~2.22); different platforms per leg.
    leg_books = {leg["book"] for leg in arb["legs"]}
    assert len(leg_books) == 2
    assert arb["roi"] > 0


def test_different_categories_do_not_group():
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=5)
    events = [
        {
            "category": "politics", "external_id": "polymarket:p1", "platform": "polymarket",
            "title": "Same wording here", "resolution_date": _iso(resolution),
            "yes_price": 0.3, "no_price": 0.7,
        },
        {
            "category": "finance", "external_id": "kalshi:k1", "platform": "kalshi",
            "title": "Same wording here", "resolution_date": _iso(resolution),
            "yes_price": 0.9, "no_price": 0.1,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()
    assert arbs == []


def test_fetch_skips_platform_on_error_but_keeps_others():
    with patch("src.core.orchestration.polymarket_scan.scan_polymarket_finite_events", side_effect=RuntimeError("boom")), \
         patch("src.scrapers.manifold_client.ManifoldClient") as MockManifold, \
         patch("src.scrapers.kalshi_client.KalshiClient") as MockKalshi:
        MockManifold.return_value.__enter__.return_value.get_finite_events.return_value = [{
            "category": "climate", "external_id": "manifold:1", "resolution_date": datetime.now(timezone.utc),
            "title": "Will it snow", "_probability": 0.2,
        }]
        MockKalshi.return_value.__enter__.return_value.get_markets.return_value = []

        events = scanning._fetch_prediction_market_events()

    assert len(events) == 1
    assert events[0]["platform"] == "manifold"
