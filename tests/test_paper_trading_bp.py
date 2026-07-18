"""Tests for /api/paper/* endpoints (paper_trading_bp) — SIMULATED money only."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.web.create_app import create_app


@pytest.fixture()
def client():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def test_wallet_endpoint_seeds_default_200(client):
    resp = client.get("/api/paper/wallet")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "paper"
    assert data["balance"] == 200.0
    assert data["initial_balance"] == 200.0


def test_wallet_endpoint_splits_balance_by_platform_for_display(client):
    resp = client.get("/api/paper/wallet")
    data = resp.get_json()
    by_platform = data["by_platform"]
    assert set(by_platform.keys()) == {"kalshi"}
    total = sum(v["balance"] for v in by_platform.values())
    assert total == pytest.approx(200.0, abs=0.01)
    for v in by_platform.values():
        assert v["label"] == "paper"


def test_run_cycle_endpoint_uses_real_scan_but_writes_only_paper_tables(client):
    with patch(
        "src.core.orchestration.scanning._scan_prediction_market_arbs",
        return_value=[],
    ), patch(
        "src.core.orchestration.scanning._fetch_prediction_market_events",
        return_value=[],
    ):
        resp = client.post("/api/paper/run-cycle?max_hours=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["opportunities_scanned"] == 0
    assert data["opened"] == 0


def test_ledger_and_bets_endpoints_empty_wallet(client):
    r1 = client.get("/api/paper/ledger?days=30")
    assert r1.status_code == 200
    assert r1.get_json()["curve"] == []

    r2 = client.get("/api/paper/bets")
    assert r2.status_code == 200
    assert r2.get_json()["bets"] == []
    assert r2.get_json()["total_count"] == 0
    assert r2.get_json()["has_more"] is False


def test_bets_endpoint_pagination(client):
    from src.core.orchestration.paper_trading_cycle import get_or_create_wallet

    w = get_or_create_wallet()
    for i in range(25):
        SimulatedBet.create(
            bet_id=f"bet-{i:03d}", wallet=w, strategy="strategy_1_universal",
            status="strategy1_awaiting_hedge", opened_at=f"2026-07-0{1 + i % 9}T00:00:{i:02d}",
        )

    r1 = client.get("/api/paper/bets?limit=10&page=1")
    d1 = r1.get_json()
    assert len(d1["bets"]) == 10
    assert d1["total_count"] == 25
    assert d1["page"] == 1
    assert d1["has_more"] is True

    r2 = client.get("/api/paper/bets?limit=10&page=3")
    d2 = r2.get_json()
    assert len(d2["bets"]) == 5  # 25 total, page 3 of size 10 -> only 5 left
    assert d2["has_more"] is False

    # No overlap between pages (offset advances correctly)
    ids_p1 = {b["bet_id"] for b in d1["bets"]}
    ids_p3 = {b["bet_id"] for b in d2["bets"]}
    assert ids_p1.isdisjoint(ids_p3)

    # Explicit offset overrides page-derived offset
    r3 = client.get("/api/paper/bets?limit=10&offset=5")
    d3 = r3.get_json()
    assert d3["offset"] == 5
    assert len(d3["bets"]) == 10


def test_bets_endpoint_excludes_no_leg_filled_statuses_by_default(client):
    """strategy1_observing / crypto15m_A_leg1_pending have NO leg executed yet
    (total_staked=0.0) and should not clutter the default history — but must
    stay retrievable via ?include_observing=true for debugging, and must not
    be deleted from the DB."""
    from src.core.orchestration.paper_trading_cycle import get_or_create_wallet

    w = get_or_create_wallet()
    SimulatedBet.create(
        bet_id="obs-1", wallet=w, strategy="strategy_1_universal",
        status="strategy1_observing", total_staked=0.0, opened_at="2026-07-01T00:00:00",
    )
    SimulatedBet.create(
        bet_id="pend-1", wallet=w, strategy="crypto_15m",
        status="crypto15m_A_leg1_pending", total_staked=0.0, opened_at="2026-07-01T00:00:01",
    )
    SimulatedBet.create(
        bet_id="live-1", wallet=w, strategy="strategy_1_universal",
        status="strategy1_awaiting_hedge", total_staked=10.0, opened_at="2026-07-01T00:00:02",
    )

    r = client.get("/api/paper/bets")
    data = r.get_json()
    ids = {b["bet_id"] for b in data["bets"]}
    assert ids == {"live-1"}
    assert data["total_count"] == 1

    r2 = client.get("/api/paper/bets?include_observing=true")
    data2 = r2.get_json()
    ids2 = {b["bet_id"] for b in data2["bets"]}
    assert ids2 == {"obs-1", "pend-1", "live-1"}
    assert data2["total_count"] == 3

    # never deleted from the DB
    assert SimulatedBet.select().where(SimulatedBet.bet_id == "obs-1").count() == 1


def test_bets_endpoint_returns_resolution_meta_and_fill_json(client):
    """Regression test for the real bug found 2026-07-08: `/api/paper/bets`
    never returned `resolution_meta` (holds the human-readable `title`) nor
    `simulated_fill_json` (holds real per-leg side/price/stake from the fill
    engine) — the frontend history had nothing to build a readable title or
    real leg detail from, only the bare `bet_id` and platform names."""
    import json
    from src.core.orchestration.paper_trading_cycle import get_or_create_wallet

    w = get_or_create_wallet()
    SimulatedBet.create(
        bet_id="bet-meta-check", wallet=w, strategy="strategy_3_same_market",
        status="settled_win", opened_at="2026-07-08T00:00:00",
        resolution_meta=json.dumps({"title": "Team X vs Team Y"}),
        simulated_fill_json=json.dumps({"legs": [
            {"platform": "kalshi", "leg_side": "yes", "avg_fill_price": 0.42, "filled_usd": 5.0, "fill_pct": 100.0},
            {"platform": "kalshi", "leg_side": "no", "avg_fill_price": 0.55, "filled_usd": 5.0, "fill_pct": 100.0},
        ]}),
    )
    r = client.get("/api/paper/bets?limit=10&page=1")
    bet = next(b for b in r.get_json()["bets"] if b["bet_id"] == "bet-meta-check")
    assert json.loads(bet["resolution_meta"])["title"] == "Team X vs Team Y"
    fill = json.loads(bet["simulated_fill_json"])
    assert len(fill["legs"]) == 2
    assert fill["legs"][0]["platform"] == "kalshi"


# ---------------------------------------------------------------------------
# /api/paper/strategy1/loss-analysis (2026-07-17): confirmed real-production
# finding that Strategy 1 "directional" resolutions are unhedged legs of
# failed arb pairs (hedge/limit leg never filled), not a deliberate
# directional strategy. See paper_trading_bp.py endpoint docstring.
# ---------------------------------------------------------------------------

def test_loss_analysis_groups_by_category_and_computes_leg_volatility(client):
    import json
    from src.core.orchestration.paper_trading_cycle import get_or_create_wallet, _S1_STRATEGY_NAME

    w = get_or_create_wallet()

    # Genuine arb, both legs filled -- baseline/comparison bucket.
    SimulatedBet.create(
        bet_id="s1-arb-1", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_arb", opened_at="2026-07-17T00:00:00",
        total_staked=10.0, pnl=0.5,
        resolution_meta=json.dumps({
            "category": "tennis", "l1_price": 0.95, "epsilon": 0.03,
            "hedge_price": 0.04, "resolved_via": "hedge_filled",
        }),
    )
    # Directional win: hedge never filled, leader ended up winning anyway.
    SimulatedBet.create(
        bet_id="s1-win-1", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_directional_win", opened_at="2026-07-17T00:00:00",
        total_staked=5.0, pnl=0.15,
        resolution_meta=json.dumps({
            "category": "tennis", "l1_price": 0.97, "epsilon": 0.03,
            "hedge_price": 0.02, "hedge_proposed_at_cycle_price": 0.05,
            "resolved_via": "price_heuristic_win_unverified",
        }),
    )
    # Directional loss: hedge never filled, leader lost -- full stake wipeout.
    SimulatedBet.create(
        bet_id="s1-loss-1", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_directional_loss", opened_at="2026-07-17T00:00:00",
        total_staked=6.5, pnl=-6.5,
        resolution_meta=json.dumps({
            "category": "tennis", "l1_price": 0.96, "epsilon": 0.03,
            "hedge_price": 0.03, "hedge_proposed_at_cycle_price": 0.08,
            "resolved_via": "price_heuristic_loss_unverified",
            "title": "Will Player A win the match?",
        }),
    )
    # Different category, to verify per-category grouping (not global mixing).
    SimulatedBet.create(
        bet_id="s1-loss-2", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_directional_loss", opened_at="2026-07-17T00:00:00",
        total_staked=3.0, pnl=-3.0,
        resolution_meta=json.dumps({
            "category": "weather", "l1_price": 0.9, "epsilon": 0.05,
            "resolved_via": "real_result_flashscore",
            "title": "Will it rain?",
        }),
    )
    # A non-strategy_1 bet must be excluded entirely (different schema).
    SimulatedBet.create(
        bet_id="other-strategy", wallet=w, strategy="strategy_3_same_market",
        status="strategy1_resolved_directional_loss", opened_at="2026-07-17T00:00:00",
        total_staked=100.0, pnl=-100.0,
        resolution_meta=json.dumps({"category": "tennis"}),
    )

    resp = client.get("/api/paper/strategy1/loss-analysis")
    assert resp.status_code == 200
    data = resp.get_json()

    assert set(data["categories"].keys()) == {"tennis", "weather"}
    # Excluded strategy must not leak into overall totals.
    assert data["overall"]["resolved_directional_loss"]["n"] == 2
    assert data["overall"]["resolved_directional_loss"]["total_pnl"] == pytest.approx(-9.5)

    tennis = data["categories"]["tennis"]
    assert tennis["resolved_arb"]["n"] == 1
    assert tennis["resolved_directional_win"]["n"] == 1
    assert tennis["resolved_directional_loss"]["n"] == 1
    assert tennis["resolved_directional_loss"]["roi_pct"] == pytest.approx(-100.0)
    # market leg: avg |l1_price - 0.5| across all 3 tennis bets (0.45, 0.47, 0.46)
    assert tennis["market_leg"]["avg_fill_dispersion_from_mid"] == pytest.approx(0.46, abs=0.01)
    assert tennis["market_leg"]["avg_epsilon"] == pytest.approx(0.03)
    # limit leg: gap data available for win (|0.02-0.05|=0.03) and loss (|0.03-0.08|=0.05)
    assert tennis["limit_leg"]["n_samples_with_gap_data"] == 2
    assert tennis["limit_leg"]["avg_gap_to_fill"] == pytest.approx(0.04, abs=0.001)

    weather = data["categories"]["weather"]
    assert weather["resolved_directional_loss"]["n"] == 1
    assert weather["limit_leg"]["n_samples_with_gap_data"] == 0
    assert weather["limit_leg"]["n_gap_unavailable"] == 1  # no hedge_price/closest at all
    # loss_bets drill-down present with the real title
    assert weather["loss_bets"][0]["title"] == "Will it rain?"

    # Worst-category-first ordering (most negative resolved_directional_loss pnl first)
    assert list(data["categories"].keys())[0] == "tennis"  # -6.5 < -3.0


def test_loss_analysis_category_filter(client):
    import json
    from src.core.orchestration.paper_trading_cycle import get_or_create_wallet, _S1_STRATEGY_NAME

    w = get_or_create_wallet()
    SimulatedBet.create(
        bet_id="s1-loss-filter", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_directional_loss", opened_at="2026-07-17T00:00:00",
        total_staked=4.0, pnl=-4.0,
        resolution_meta=json.dumps({"category": "basketball"}),
    )
    SimulatedBet.create(
        bet_id="s1-loss-other", wallet=w, strategy=_S1_STRATEGY_NAME,
        status="strategy1_resolved_directional_loss", opened_at="2026-07-17T00:00:00",
        total_staked=2.0, pnl=-2.0,
        resolution_meta=json.dumps({"category": "tennis"}),
    )
    resp = client.get("/api/paper/strategy1/loss-analysis?category=basketball")
    data = resp.get_json()
    assert set(data["categories"].keys()) == {"basketball"}


def test_loss_analysis_empty_wallet_returns_empty_shape(client):
    resp = client.get("/api/paper/strategy1/loss-analysis?label=nonexistent-label")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["categories"] == {}
    assert data["overall"] is None
