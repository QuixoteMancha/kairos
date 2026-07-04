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


def test_arb_profitable_gross_but_unprofitable_net_of_fees_is_discarded():
    """A small implied-prob gap can look like an arb before fees, but Kalshi's
    ~50/50-price taker fee (worst case ~1.75c on a $1 contract) can erase a
    thin edge. imp=0.985 -> gross roi=1.5% (just above PREDICTION_MARKET_MIN_ROI_PCT),
    but with both legs priced near 0.50 on kalshi+polymarket the fee eats most
    of it, dropping below PREDICTION_MARKET_MIN_NET_ROI_PCT."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        {
            "category": "economics", "external_id": "kalshi:thin-edge", "platform": "kalshi",
            "title": "Will the Fed cut rates this meeting",
            "resolution_date": _iso(resolution), "yes_price": 0.495, "no_price": 0.505,
        },
        {
            "category": "economics", "external_id": "polymarket:thin-edge", "platform": "polymarket",
            "title": "Will the Fed cut rates this meeting?",
            "resolution_date": _iso(resolution + timedelta(minutes=3)), "yes_price": 0.505, "no_price": 0.49,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()
    assert arbs == []


def test_arb_still_profitable_net_of_fees_is_kept():
    """A wide enough divergence survives fees and is reported with roi (net)
    and roi_gross both present, net <= gross."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        {
            # yes+no sums to 1.02 (>=0.999) so this platform's OWN market has
            # no internal same-market arb — only the cross-platform divergence
            # against polymarket below should fire.
            "category": "economics", "external_id": "kalshi:wide-edge", "platform": "kalshi",
            "title": "Will unemployment exceed 5 percent this report",
            "resolution_date": _iso(resolution), "yes_price": 0.30, "no_price": 0.72,
        },
        {
            "category": "economics", "external_id": "polymarket:wide-edge", "platform": "polymarket",
            "title": "Will unemployment exceed 5 percent this report?",
            "resolution_date": _iso(resolution + timedelta(minutes=3)), "yes_price": 0.60, "no_price": 0.42,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["roi"] > 0
    assert arb["roi"] <= arb["roi_gross"]
    assert arb["fee_cost_pct"] >= 0


def test_manifold_vs_real_money_arb_is_rejected():
    """Manifold uses mana (virtual, non-withdrawable) — even a wide price
    divergence between Manifold and a real-money platform is not a genuine
    capital-hedged arb, since a Manifold "win" can't offset a real loss on
    Polymarket/Kalshi. Only manifold+polymarket available here (no kalshi),
    so the best legs are forced onto the manifold/polymarket pair."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        {
            "category": "crypto", "external_id": "manifold:btc-100k", "platform": "manifold",
            "title": "Will BTC exceed 100k by Friday",
            "resolution_date": _iso(resolution), "yes_price": 0.20, "no_price": 0.75,
        },
        {
            # yes+no sums to 1.01 (>=0.999) so polymarket's OWN market has no
            # internal same-market arb either — manifold is excluded from
            # same-market arb entirely (mana), regardless of its own prices.
            "category": "crypto", "external_id": "polymarket:btc-100k", "platform": "polymarket",
            "title": "Will BTC exceed 100k by Friday?",
            "resolution_date": _iso(resolution + timedelta(minutes=3)), "yes_price": 0.70, "no_price": 0.31,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()
    assert arbs == []


def test_overlapping_cross_platform_and_same_market_arb_keeps_higher_roi_only():
    """If the same (platform, external_id) leg is claimed by both a
    cross-platform opportunity and a same-market opportunity, only the
    higher-net-roi one should survive — they'd compete for the same
    order-book depth if both were ever executed."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        {
            # Kalshi's own market: yes+no = 0.90 -> a real same-market arb on
            # its own (roi ~10% gross). This same kalshi:evt1 leg is ALSO the
            # best "yes" leg of a much wider cross-platform divergence below.
            "category": "politics", "external_id": "kalshi:evt1", "platform": "kalshi",
            "title": "Will the measure pass",
            "resolution_date": _iso(resolution), "yes_price": 0.30, "no_price": 0.60,
        },
        {
            # Polymarket's own market: yes+no = 1.01 -> no internal arb here,
            # but its "no" leg pairs with kalshi's "yes" for a wide (~37%
            # gross) cross-platform arb.
            "category": "politics", "external_id": "polymarket:evt1", "platform": "polymarket",
            "title": "Will the measure pass?",
            "resolution_date": _iso(resolution + timedelta(minutes=3)), "yes_price": 0.68, "no_price": 0.33,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()

    # Only one opportunity should survive: the cross-platform one (much
    # wider edge), not the same-market kalshi-only one — both claim the
    # kalshi:evt1 leg.
    assert len(arbs) == 1
    survivor = arbs[0]
    assert survivor["tipo"] == "prediction_market_cross_platform_arb"
    leg_ext_ids = {leg.get("external_id") for leg in survivor["legs"]}
    assert "kalshi:evt1" in leg_ext_ids


def test_non_overlapping_arbs_are_both_kept():
    """Two opportunities touching entirely different markets must both be
    reported — no false dedup when there's no shared leg."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        # Cross-platform pair, event A (politics) — no internal arb on either leg.
        {
            "category": "politics", "external_id": "kalshi:eventA", "platform": "kalshi",
            "title": "Event A happens",
            "resolution_date": _iso(resolution), "yes_price": 0.30, "no_price": 0.68,
        },
        {
            "category": "politics", "external_id": "polymarket:eventA", "platform": "polymarket",
            "title": "Event A happens?",
            "resolution_date": _iso(resolution + timedelta(minutes=3)), "yes_price": 0.65, "no_price": 0.33,
        },
        # Unrelated same-market arb, event B (economics), single platform only.
        {
            "category": "economics", "external_id": "kalshi:eventB", "platform": "kalshi",
            "title": "Event B resolves yes",
            "resolution_date": _iso(resolution + timedelta(hours=1)), "yes_price": 0.25, "no_price": 0.55,
        },
    ]
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events):
        arbs = scanning._scan_prediction_market_arbs()

    types = {a["tipo"] for a in arbs}
    assert len(arbs) == 2
    assert "prediction_market_cross_platform_arb" in types
    assert "same_market_yes_no_arb" in types
