"""Tests for src/core/orchestration/paper_trading_cycle.py — the Fase D
wiring of paper-trading (SIMULATED money only). Uses injected opportunities/
clients so no real network call happens."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.database.database import db, init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle


@pytest.fixture(autouse=True)
def _clean_paper_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def _fake_opp(tipo="same_market_yes_no_arb", roi=6.4, ext_id="evt-1", book="polymarket"):
    return {
        "tipo": tipo,
        "roi": roi,
        "legs": [
            {"side": "yes", "book": book, "odd": 1.5, "external_id": ext_id, "polymarket_token_id": "tok-yes"},
            {"side": "no", "book": book, "odd": 3.2, "external_id": ext_id, "polymarket_token_id": "tok-no"},
        ],
    }


def _mock_fill_engine(fillable: bool):
    fake_legs = [
        {"leg_side": "yes", "fill_pct": 100.0 if fillable else 10.0, "classification": "full_fill" if fillable else "partial_fill"},
        {"leg_side": "no", "fill_pct": 100.0 if fillable else 10.0, "classification": "full_fill" if fillable else "partial_fill"},
    ]
    return patch(
        "src.core.orchestration.paper_fill_engine.simulate_arb_legs_multi_platform",
        return_value={"legs": fake_legs},
    )


def test_get_or_create_wallet_seeds_200usdc():
    w = cycle.get_or_create_wallet("test-wallet-1", initial_balance=200.0)
    assert w.platform == "paper"
    assert w.balance == 200.0
    assert w.equity == 200.0
    w2 = cycle.get_or_create_wallet("test-wallet-1", initial_balance=200.0)
    assert w2.id == w.id  # idempotent, doesn't reset


def test_run_cycle_opens_and_settles_fillable_opp_compounding_balance():
    opp = _fake_opp(roi=6.4, ext_id="evt-fill-1")
    with _mock_fill_engine(fillable=True):
        result = cycle.run_paper_trading_cycle(
            wallet_label="test-wallet-2", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0, stake_fraction=0.05,
        )
    assert result["opened"] == 1
    assert result["settled_win"] == 1
    assert result["unfillable"] == 0
    # BUG REAL 2026-07-16 fix: sizing now uses `_available_capital_for_new_stake`
    # (reserves 10% of equity before sizing new stakes), not raw balance.
    # available = 200 - 200*0.10 = 180; stake = 180*0.05=9, pnl = 9*0.064=0.576
    # -> balance/equity = 200.576
    assert result["balance"] == pytest.approx(200.576, abs=0.01)
    assert result["equity"] == pytest.approx(200.576, abs=0.01)

    bet = SimulatedBet.get(SimulatedBet.strategy == "strategy_3_same_market")
    assert bet.status == "settled_win"
    assert bet.pnl == pytest.approx(0.576, abs=0.01)


def test_last_cycle_summary_persisted_even_with_zero_opportunities():
    # "Nothing found" must still be recorded — silence is indistinguishable
    # from a dead worker, this is the evidence the dashboard reads.
    result = cycle.run_paper_trading_cycle(wallet_label="test-wallet-7", _opportunities=[], _raw_events=[], min_roi_pct=2.0)
    assert result["opportunities_scanned"] == 0
    assert "ran_at" in result

    persisted = cycle.get_last_cycle_summary()
    assert persisted is not None
    assert persisted["opportunities_scanned"] == 0
    assert persisted["wallet_label"] == "test-wallet-7"


def test_run_cycle_marks_unfillable_without_moving_capital():
    opp = _fake_opp(roi=6.4, ext_id="evt-nofill-1")
    with _mock_fill_engine(fillable=False):
        result = cycle.run_paper_trading_cycle(
            wallet_label="test-wallet-3", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0, stake_fraction=0.05,
        )
    assert result["opened"] == 0
    assert result["unfillable"] == 1
    assert result["balance"] == pytest.approx(200.0)
    bet = SimulatedBet.get(SimulatedBet.strategy == "strategy_3_same_market")
    assert bet.status == "unfillable"


def test_run_cycle_skips_low_roi_and_duplicates():
    low_roi_opp = _fake_opp(roi=0.5, ext_id="evt-lowroi")
    result = cycle.run_paper_trading_cycle(
        wallet_label="test-wallet-4", _opportunities=[low_roi_opp], _raw_events=[], min_roi_pct=2.0,
    )
    assert result["skipped_low_roi"] == 1
    assert result["opened"] == 0

    opp = _fake_opp(roi=6.4, ext_id="evt-dupe-1")
    with _mock_fill_engine(fillable=True):
        r1 = cycle.run_paper_trading_cycle(wallet_label="test-wallet-5", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0)
        r2 = cycle.run_paper_trading_cycle(wallet_label="test-wallet-5", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0)
    assert r1["opened"] == 1
    assert r2["opened"] == 0
    assert r2["skipped_dupe"] == 1


def test_settled_bet_persists_readable_title_not_just_raw_external_ids():
    """Regression test for the real bug found 2026-07-08: bets from
    Strategy 2/3/football had no human-readable `title` in resolution_meta,
    so the frontend history fell back to displaying the raw `bet_id`
    (`f"{tipo}:{ext_id}-{ext_id}..."`) — often a very long, unreadable
    concatenation of Polymarket/Kalshi external ids. Strategy 1 already did
    this correctly; strategies 2/3/football must too, using the
    opportunity's own `fixture`/`title` field."""
    import json
    opp = _fake_opp(roi=6.4, ext_id="evt-title-1")
    opp["fixture"] = "Team A vs Team B"
    with _mock_fill_engine(fillable=True):
        cycle.run_paper_trading_cycle(
            wallet_label="test-wallet-title", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0,
        )
    bet = SimulatedBet.get(SimulatedBet.strategy == "strategy_3_same_market")
    meta = json.loads(bet.resolution_meta)
    assert meta["title"] == "Team A vs Team B"
    # bet_id itself must stay untouched (dedup key derived from ext_ref)
    assert "evt-title-1" in bet.bet_id


# --- Real-fill-slippage PnL recompute (2026-07-08) -------------------------
#
# Root cause fixed here: `pnl` for Strategy 2/3/football was computed purely
# from the scan-time quoted `roi_pct` (top-of-book odds), completely
# ignoring the REAL `avg_fill_price` per leg that `paper_fill_engine.py`
# already computes by walking the real order book. `_recompute_pnl_from_fills`
# now uses that real per-leg price when available, falling back to the
# quoted roi_pct when any leg lacks usable fill data.

def test_recompute_pnl_uses_real_slippage_when_available():
    """With real slippage (avg_fill_price worse than the quoted target), the
    real net ROI must come out LOWER than an inflated quoted roi_pct —
    and the function must return this real figure, not the quoted one."""
    fill_result_worse = {
        "legs": [
            {"leg_side": "yes", "platform": "polymarket", "avg_fill_price": 0.70, "classification": "full_fill"},
            {"leg_side": "no", "platform": "polymarket", "avg_fill_price": 0.29, "classification": "full_fill"},
        ]
    }
    pnl_worse, net_roi_worse = cycle._recompute_pnl_from_fills(fill_result_worse, stake_usd=10.0, roi_pct_fallback=50.0)
    assert net_roi_worse is not None
    # real imp = 0.99 -> roi_gross = (1-0.99)/0.99*100 ~= 1.01%, minus fees,
    # must be far lower than the inflated 50% fallback it would otherwise use
    assert net_roi_worse < 50.0
    assert pnl_worse == pytest.approx(10.0 * (net_roi_worse / 100.0), abs=1e-6)


def test_recompute_pnl_falls_back_to_quoted_roi_when_leg_missing_fill_price():
    """Fail-closed: if any leg is missing avg_fill_price (no_data,
    unsupported_amm, network error), never invent a real-fill number — use
    the quoted roi_pct unchanged."""
    fill_result = {
        "legs": [
            {"leg_side": "yes", "platform": "polymarket", "avg_fill_price": 0.65, "classification": "full_fill"},
            {"leg_side": "no", "platform": "kalshi", "avg_fill_price": None, "classification": "no_data"},
        ]
    }
    pnl, net_roi_real = cycle._recompute_pnl_from_fills(fill_result, stake_usd=10.0, roi_pct_fallback=6.4)
    assert net_roi_real is None
    assert pnl == pytest.approx(10.0 * (6.4 / 100.0), abs=1e-9)


def test_recompute_pnl_matches_quoted_roi_on_perfect_fill_sanity_check():
    """Sanity check: when avg_fill_price equals the exact target/quoted
    price for both legs (zero slippage, perfect fill), the real recompute
    must reproduce the same net ROI the scan-time quoted formula would give
    for that same price pair — proving the new formula is consistent with
    the old one in the ideal case, not just a different approximation."""
    from src.core.orchestration.platform_fees import leg_fee_pct

    yes_price, no_price = 1 / 1.5, 1 / 3.2  # exactly what _fake_opp()'s odds imply
    imp = yes_price + no_price
    roi_gross = (1 - imp) / imp * 100
    fee_cost_pct = (
        leg_fee_pct("polymarket", yes_price) * yes_price
        + leg_fee_pct("polymarket", no_price) * no_price
    ) / imp * 100
    expected_net_roi = roi_gross - fee_cost_pct

    fill_result = {
        "legs": [
            {"leg_side": "yes", "platform": "polymarket", "avg_fill_price": yes_price, "classification": "full_fill"},
            {"leg_side": "no", "platform": "polymarket", "avg_fill_price": no_price, "classification": "full_fill"},
        ]
    }
    pnl, net_roi_real = cycle._recompute_pnl_from_fills(fill_result, stake_usd=10.0, roi_pct_fallback=999.0)
    assert net_roi_real == pytest.approx(expected_net_roi, abs=1e-3)
    assert pnl == pytest.approx(10.0 * (expected_net_roi / 100.0), abs=1e-3)


def test_run_cycle_settles_with_real_fill_pnl_end_to_end():
    """End-to-end: run_paper_trading_cycle must persist bet.pnl computed
    from real avg_fill_price (via _recompute_pnl_from_fills), not the
    quoted roi_pct, when the (mocked) fill engine reports real prices."""
    opp = _fake_opp(roi=50.0, ext_id="evt-realfill-1")  # deliberately inflated quoted roi
    fake_legs = [
        {"leg_side": "yes", "platform": "polymarket", "avg_fill_price": 0.70, "fill_pct": 100.0, "classification": "full_fill"},
        {"leg_side": "no", "platform": "polymarket", "avg_fill_price": 0.29, "fill_pct": 100.0, "classification": "full_fill"},
    ]
    with patch(
        "src.core.orchestration.paper_fill_engine.simulate_arb_legs_multi_platform",
        return_value={"legs": fake_legs},
    ):
        cycle.run_paper_trading_cycle(
            wallet_label="test-wallet-realfill", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0, stake_fraction=0.05,
        )
    bet = SimulatedBet.get(SimulatedBet.bet_id.contains("evt-realfill-1"))
    assert bet.status == "settled_win"
    # stake = 200*0.05 = 10; real imp = 0.99 -> roi_gross ~1.01%, well below
    # the quoted 50% -> pnl must be far smaller than 10*0.50=5.0
    assert bet.pnl < 1.0
    assert bet.pnl > -1.0


def test_estimate_strategy1_epsilon_uses_dwell_time_for_football():
    """First live wiring of the dwell-time models into Strategy 1's level
    placement: a football/soccer candidate with a 'vs'-shaped title must use
    the dwell-time model's dispersion-derived epsilon, not the fixed
    EPSILON_INITIAL constant."""
    from src.core.strategies.event_volatility.base import DwellBand
    from src.core.strategies.universal_limit_arb import EPSILON_INITIAL

    bands = [
        DwellBand(low=0.40, high=0.60, side="Team A", expected_time_fraction=1.0),
        DwellBand(low=0.40, high=0.60, side="Team B", expected_time_fraction=1.0),
    ]
    fake_model = MagicMock()
    fake_model.estimate.return_value = bands
    event = {"category": "football", "title": "Team A vs Team B"}
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=fake_model):
        eps, source = cycle._estimate_strategy1_epsilon(event)
    assert source == "dwell_time:football"
    assert eps != EPSILON_INITIAL


def test_estimate_strategy1_epsilon_falls_back_when_no_team_names():
    from src.core.strategies.universal_limit_arb import EPSILON_INITIAL

    event = {"category": "football", "title": "Some prop bet title"}
    eps, source = cycle._estimate_strategy1_epsilon(event)
    assert eps == EPSILON_INITIAL
    assert source.startswith("fallback_generic:no_team_names")


def test_estimate_strategy1_epsilon_falls_back_for_unwired_category():
    """Tennis has no live context source wired into this cycle yet — must
    fall back to the generic epsilon, never guess/fail."""
    from src.core.strategies.universal_limit_arb import EPSILON_INITIAL

    event = {"category": "tennis", "title": "Player A vs Player B"}
    eps, source = cycle._estimate_strategy1_epsilon(event)
    assert eps == EPSILON_INITIAL
    assert "tennis" in source


def test_estimate_strategy1_epsilon_never_raises_on_model_error():
    from src.core.strategies.universal_limit_arb import EPSILON_INITIAL

    broken_model = MagicMock()
    broken_model.estimate.side_effect = RuntimeError("boom")
    event = {"category": "football", "title": "Team A vs Team B"}
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=broken_model):
        eps, source = cycle._estimate_strategy1_epsilon(event)
    assert eps == EPSILON_INITIAL
    assert source.startswith("fallback_generic:model_error")


def test_never_touches_real_wallet_tables():
    from src.database.models.wallet import Wallet, WalletLedger

    before_w = Wallet.select().count()
    before_l = WalletLedger.select().count()
    opp = _fake_opp(roi=6.4, ext_id="evt-isolation-1")
    with _mock_fill_engine(fillable=True):
        cycle.run_paper_trading_cycle(wallet_label="test-wallet-6", _opportunities=[opp], _raw_events=[], min_roi_pct=2.0)
    after_w = Wallet.select().count()
    after_l = WalletLedger.select().count()
    assert before_w == after_w
    assert before_l == after_l
