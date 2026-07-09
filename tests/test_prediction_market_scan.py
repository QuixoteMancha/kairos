"""Tests for the prediction-markets cross-platform arb scan
(_scan_prediction_market_arbs / _fetch_prediction_market_events in scanning.py).

Uses a synthetic event that "appears" on 2-3 platforms with different prices,
mocking each platform's fetch function so no network calls happen.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.core.orchestration import scanning


@pytest.fixture(autouse=True)
def _disable_football_fetchers():
    with patch("src.core.orchestration.scanning._fetch_polymarket_3way_football_events", return_value=[]), \
         patch("src.core.orchestration.scanning._fetch_kalshi_3way_football_events", return_value=[]):
        yield


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _mock_poly_client(token_prices: dict):
    """Patches PolymarketClient (as imported by cross_platform_deep's
    deep-verify step) so Strategy-2 candidates carrying a polymarket leg with
    clob_token_ids can pass fail-closed re-verification in these
    scanning-level tests without a real network call.

    `token_prices`: {token_id: best_ask_price} — mirrors the price the test's
    fast-scan snapshot already assumed, so the deep-verify step confirms
    (rather than changes) the expected roi in tests that aren't specifically
    exercising the deep-verify divergence/failure behavior (see
    tests/test_cross_platform_deep.py for those).
    """
    client = MagicMock()
    client.get_order_book.side_effect = lambda token_id: (
        {"asks": [(token_prices[token_id], 1000.0)], "bids": []}
        if token_id in token_prices else {"asks": [], "bids": []}
    )
    return patch("src.scrapers.polymarket_client.PolymarketClient", return_value=client)


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
            "clob_token_ids": ["yes-same-event", "no-same-event"],
        },
        {
            "category": "politics", "external_id": "kalshi:SAME-EVENT", "platform": "kalshi",
            "title": "Will candidate A win the special election?",
            "resolution_date": _iso(resolution + timedelta(minutes=5)), "yes_price": 0.40, "no_price": 0.60,
        },
        {
            "category": "politics", "external_id": "legacy_virtual:999", "platform": "legacy_virtual",
            "title": "Candidate A wins special election",
            "resolution_date": _iso(resolution - timedelta(minutes=10)), "yes_price": 0.50, "no_price": 0.50,
        },
    ]
    # Best NO leg is polymarket (no_price=0.45 -> ask 0.45) — deep-verify
    # confirms the same price the fast-scan already assumed.
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events), \
            _mock_poly_client({"no-same-event": 0.45}):
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


def test_fetch_includes_crypto_5m_and_15m_events_with_distinct_categories():
    """Polymarket crypto up/down 5m/15m markets must be fetched and tagged
    with a distinguishable category (not generic 'prediction_market') so
    same_market_arb evaluates them and the frontend can badge them."""
    crypto_5m_payload = {"markets": [{
        "condition_id": "0x5m", "question": "Bitcoin Up or Down - 5m window",
        "end_date": _iso(datetime.now(timezone.utc) + timedelta(minutes=5)),
        "yes_price": 0.4, "no_price": 0.55, "clob_token_ids": ["tok_yes_5m", "tok_no_5m"],
    }]}
    crypto_15m_payload = {"markets": [{
        "condition_id": "0x15m", "question": "Ethereum Up or Down - 15m window",
        "end_date": _iso(datetime.now(timezone.utc) + timedelta(minutes=15)),
        "yes_price": 0.5, "no_price": 0.45, "clob_token_ids": ["tok_yes_15m", "tok_no_15m"],
    }]}
    with patch.object(scanning, "_fetch_prediction_market_events", wraps=scanning._fetch_prediction_market_events) as _wrap, \
         patch("src.core.orchestration.polymarket_scan.scan_polymarket_finite_events", return_value={"markets": []}), \
         patch("src.core.orchestration.polymarket_scan.scan_polymarket_crypto_5m", return_value=crypto_5m_payload), \
         patch("src.core.orchestration.polymarket_scan.scan_polymarket_crypto_15m", return_value=crypto_15m_payload), \
         patch("src.scrapers.kalshi_client.KalshiClient") as MockKalshi:
        MockKalshi.return_value.__enter__.return_value.get_markets.return_value = []

        events = scanning._fetch_prediction_market_events()

    categories = {e["category"] for e in events}
    assert categories == {"crypto_5m", "crypto_15m"}
    assert all(e["platform"] == "polymarket" for e in events)


def test_crypto_5m_event_is_evaluated_by_same_market_arb():
    """A crypto_5m event with a real yes+no<1 gap must still be picked up by
    scan_same_market_arb_fast — no category-based gate silently excludes it.

    Polymarket same-market candidates are re-verified against the real CLOB
    order book (Bug 2 fix) before being returned, so the book lookup is
    mocked here to return depth consistent with the snapshot prices — this
    test is about the category not being gated, not about the deep-check
    arithmetic itself (see test_same_market_arb.py for that)."""
    events = [{
        "category": "crypto_5m", "external_id": "polymarket:0xcrypto5m", "platform": "polymarket",
        "title": "Bitcoin Up or Down - 5m window",
        "resolution_date": _iso(datetime.now(timezone.utc) + timedelta(minutes=4)),
        "yes_price": 0.45, "no_price": 0.45, "clob_token_ids": ["tok_yes", "tok_no"],
    }]

    class _FakeClient:
        def get_order_book(self, token_id):
            return {"asks": [(0.45, 1000.0)], "bids": [(0.44, 1000.0)]}

        def close(self):
            pass

    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events), \
         patch("src.scrapers.polymarket_client.PolymarketClient", return_value=_FakeClient()):
        arbs = scanning._scan_prediction_market_arbs()
    assert len(arbs) == 1
    assert arbs[0]["tipo"] == "same_market_yes_no_arb"
    assert arbs[0]["category"] == "crypto_5m"


def test_refresh_prediction_market_arbs_in_cache_replaces_only_pm_arbs():
    """The CB_SCAN_TTL cache-hit fast path must recompute prediction-market
    arbs fresh (crypto 5m/15m windows can't tolerate 180s staleness) while
    leaving unrelated cached sports arbs untouched."""
    stale_pm_arb = {"tipo": "same_market_yes_no_arb", "roi": 1.0, "legs": []}
    sports_arb = {"tipo": "cross_book_arb_2way", "roi": 5.0, "legs": []}
    fresh_pm_arb = {"tipo": "same_market_yes_no_arb", "roi": 9.9, "category": "crypto_5m", "legs": []}

    cached = {"ts": "old", "arbs": [sports_arb, stale_pm_arb]}
    with patch.object(scanning, "_scan_prediction_market_arbs", return_value=[fresh_pm_arb]):
        refreshed = scanning._refresh_prediction_market_arbs_in_cache(cached)

    assert sports_arb in refreshed["arbs"]
    assert stale_pm_arb not in refreshed["arbs"]
    assert fresh_pm_arb in refreshed["arbs"]


def test_fetch_skips_platform_on_error_but_keeps_others():
    with patch("src.core.orchestration.polymarket_scan.scan_polymarket_finite_events", return_value={"markets": [{
        "condition_id": "pm:1", "question": "Will it snow", "end_date": _iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        "yes_price": 0.6, "no_price": 0.4,
    }]}), \
         patch("src.core.orchestration.polymarket_scan.scan_polymarket_crypto_5m", return_value={"markets": []}), \
         patch("src.core.orchestration.polymarket_scan.scan_polymarket_crypto_15m", return_value={"markets": []}), \
         patch("src.scrapers.kalshi_client.KalshiClient", side_effect=RuntimeError("boom")):
        events = scanning._fetch_prediction_market_events()

    assert len(events) == 1
    assert events[0]["platform"] == "polymarket"


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
            "clob_token_ids": ["yes-wide-edge", "no-wide-edge"],
        },
    ]
    # Best yes = kalshi (0.30), best no = polymarket (0.42) — deep-verify
    # confirms the polymarket "no" leg's ask matches the snapshot.
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events), \
            _mock_poly_client({"no-wide-edge": 0.42}):
        arbs = scanning._scan_prediction_market_arbs()
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb["roi"] > 0
    assert arb["roi"] <= arb["roi_gross"]
    assert arb["fee_cost_pct"] >= 0


def test_legacy_non_real_money_vs_real_money_arb_is_rejected():
    """Legacy virtual platforms use non-withdrawable credits — even a wide price
    divergence between that legacy platform and a real-money platform is not a
    genuine capital-hedged arb, since a virtual win can't offset a real loss on
    Polymarket/Kalshi. Only legacy_virtual+polymarket is available here (no kalshi),
    so the best legs are forced onto the legacy_virtual/polymarket pair."""
    now = datetime.now(timezone.utc)
    resolution = now + timedelta(hours=6)
    events = [
        {
            "category": "crypto", "external_id": "legacy_virtual:btc-100k", "platform": "legacy_virtual",
            "title": "Will BTC exceed 100k by Friday",
            "resolution_date": _iso(resolution), "yes_price": 0.20, "no_price": 0.75,
        },
        {
            # yes+no sums to 1.01 (>=0.999) so polymarket's OWN market has no
            # internal same-market arb either — legacy_virtual is excluded from
            # same-market arb entirely (non-real-money), regardless of its own prices.
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
            "clob_token_ids": ["yes-evt1", "no-evt1"],
        },
    ]
    # Best yes = kalshi (0.30 -> odds 3.33), best no = polymarket (0.33 ->
    # odds ~3.03) — deep-verify confirms the polymarket "no" leg's ask
    # matches the snapshot.
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events), \
            _mock_poly_client({"no-evt1": 0.33}):
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
            "clob_token_ids": ["yes-eventA", "no-eventA"],
        },
        # Unrelated same-market arb, event B (economics), single platform only.
        {
            "category": "economics", "external_id": "kalshi:eventB", "platform": "kalshi",
            "title": "Event B resolves yes",
            "resolution_date": _iso(resolution + timedelta(hours=1)), "yes_price": 0.25, "no_price": 0.55,
        },
    ]
    # Best yes = kalshi (0.30 -> odds 3.33), best no = polymarket (0.33 ->
    # odds ~3.03) — deep-verify confirms the polymarket "no" leg's ask.
    with patch.object(scanning, "_fetch_prediction_market_events", return_value=events), \
            _mock_poly_client({"no-eventA": 0.33}):
        arbs = scanning._scan_prediction_market_arbs()

    types = {a["tipo"] for a in arbs}
    assert len(arbs) == 2
    assert "prediction_market_cross_platform_arb" in types
    assert "same_market_yes_no_arb" in types



def test_find_dwell_time_liquidity_overlaps_detects_shared_leg():
    """Regression for the dwell-time shared-liquidity detection gap (Vesper
    audit, alto severity): a DwellTimeProposal row and a cross-platform/
    same-market opportunity sharing the same (book, external_id) key must be
    reported by find_dwell_time_liquidity_overlaps, even though dwell-time
    proposals never go through _dedupe_arbs_by_shared_leg."""
    from src.database.database import DwellTimeProposal, init_db

    init_db()
    proposal = DwellTimeProposal.create(
        timestamp=datetime.now(timezone.utc), fixture="Lakers vs Celtics",
        category="basketball", side="team_a", proposed_price=0.4,
        expected_time_fraction=0.5, implied_edge_pct=0.05, implied_edge_gross_pct=0.06,
        book="polymarket", external_id="tok_shared_123",
    )
    opportunities = [{
        "tipo": "same_market_yes_no_arb", "roi": 3.0,
        "legs": [{"book": "polymarket", "external_id": "tok_shared_123"},
                 {"book": "polymarket", "external_id": "tok_other_456"}],
    }]

    overlaps = scanning.find_dwell_time_liquidity_overlaps(opportunities)

    assert len(overlaps) == 1
    assert overlaps[0]["book"] == "polymarket"
    assert overlaps[0]["external_id"] == "tok_shared_123"
    assert overlaps[0]["opportunity_indices"] == [0]
    assert proposal.id in overlaps[0]["dwell_time_proposal_ids"]


def test_find_dwell_time_liquidity_overlaps_empty_when_no_shared_leg():
    """No overlap reported when the dwell-time proposal's liquidity key
    doesn't match anything in the current cross-platform/same-market scan."""
    from src.database.database import DwellTimeProposal, init_db

    init_db()
    DwellTimeProposal.create(
        timestamp=datetime.now(timezone.utc), fixture="Yankees vs Red Sox",
        category="baseball", side="team_a", proposed_price=0.4,
        expected_time_fraction=0.5, implied_edge_pct=0.05, implied_edge_gross_pct=0.06,
        book="polymarket", external_id="tok_unrelated_999",
    )
    opportunities = [{
        "tipo": "same_market_yes_no_arb", "roi": 3.0,
        "legs": [{"book": "polymarket", "external_id": "tok_other_456"}],
    }]

    assert scanning.find_dwell_time_liquidity_overlaps(opportunities) == []
