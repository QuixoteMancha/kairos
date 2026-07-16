"""Tests for Fase 3 (docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md):
`classify_regime()`/`decide_crypto15m_action()`/`compute_asymmetric_hedge_level()`
(src/core/strategies/event_volatility/crypto_15m_regime.py) and the two
standalone crypto_15m Strategy 1 sub-strategy passes
(`_run_strategy1_crypto15m_a_pass`/`_run_strategy1_crypto15m_b_pass` in
paper_trading_cycle.py). Neither pass is wired into `run_paper_trading_cycle()`
yet (Fase 4) -- these tests call them directly with injected raw events."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.core.strategies.event_volatility.crypto_15m import CrossingEstimate
from src.core.strategies.event_volatility.crypto_15m_regime import (
    classify_regime, decide_crypto15m_action, compute_asymmetric_hedge_level,
)


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


# ── classify_regime / decide_crypto15m_action ──────────────────────────────

def test_classify_regime_lateral():
    assert classify_regime(momentum_zscore=0.3, stoch_k=55, stoch_d=50, price_direction=1.0) == "lateral"


def test_classify_regime_trending():
    assert classify_regime(momentum_zscore=2.0, stoch_k=80, stoch_d=75, price_direction=1.0) == "trending"


def test_classify_regime_trending_middle_band_not_lateral():
    # 1.0 <= |z| < 1.5 folds into "trending" per documented design decision.
    assert classify_regime(momentum_zscore=1.2, stoch_k=60, stoch_d=55, price_direction=1.0) == "trending"


def test_classify_regime_divergent_bearish():
    # price up but %K < %D -> bearish divergence, checked before magnitude bands.
    assert classify_regime(momentum_zscore=0.2, stoch_k=40, stoch_d=60, price_direction=1.0) == "divergent"


def test_classify_regime_divergent_bullish():
    assert classify_regime(momentum_zscore=-0.2, stoch_k=60, stoch_d=40, price_direction=-1.0) == "divergent"


def test_decide_action_lateral_routes_to_a():
    d = decide_crypto15m_action(momentum_zscore=0.1, stoch=(50, 50), price_direction=1.0, seconds_remaining=600)
    assert d.action == "A"


def test_decide_action_trending_routes_to_b():
    d = decide_crypto15m_action(momentum_zscore=2.0, stoch=(80, 70), price_direction=1.0, seconds_remaining=600)
    assert d.action == "B"


def test_decide_action_divergent_routes_to_b():
    d = decide_crypto15m_action(momentum_zscore=0.1, stoch=(40, 60), price_direction=1.0, seconds_remaining=600)
    assert d.action == "B"


@pytest.mark.parametrize("kwargs", [
    dict(momentum_zscore=None, stoch=(50, 50), price_direction=1.0, seconds_remaining=600),
    dict(momentum_zscore=0.1, stoch=None, price_direction=1.0, seconds_remaining=600),
    dict(momentum_zscore=0.1, stoch=(50, 50), price_direction=None, seconds_remaining=600),
    dict(momentum_zscore=0.1, stoch=(50, 50), price_direction=1.0, seconds_remaining=None),
])
def test_decide_action_fails_closed_on_missing_data(kwargs):
    d = decide_crypto15m_action(**kwargs)
    assert d.action is None


def test_decide_action_fails_closed_near_close():
    d = decide_crypto15m_action(momentum_zscore=0.1, stoch=(50, 50), price_direction=1.0, seconds_remaining=30)
    assert d.action is None
    assert "remaining" in d.reason


# ── compute_asymmetric_hedge_level ─────────────────────────────────────────

def test_hedge_level_fallback_when_uncalibrated():
    result = compute_asymmetric_hedge_level(leg1_fill_price=0.60, crossing_estimate=None)
    assert result.source == "fallback_fixed_markup"
    assert result.price == pytest.approx(0.40 + 0.03, abs=1e-6)


def test_hedge_level_fallback_when_estimate_not_calibrated():
    est = CrossingEstimate(calibrated=False, probability=None, sample_count=3, reason="not enough samples")
    result = compute_asymmetric_hedge_level(leg1_fill_price=0.70, crossing_estimate=est)
    assert result.source == "fallback_fixed_markup"
    assert result.price == pytest.approx(0.30 + 0.03, abs=1e-6)


def test_hedge_level_calibrated_path_tighter_with_high_confidence():
    est = CrossingEstimate(calibrated=True, probability=0.95, sample_count=50, reason="calibrated")
    result = compute_asymmetric_hedge_level(leg1_fill_price=0.60, crossing_estimate=est)
    assert result.source == "calibrated"
    # high confidence -> markup shrinks toward 0, price close to naive complement 0.40
    assert result.price < 0.60 - 0.60 + 0.40 + 0.03  # strictly less markup than fallback
    assert result.price > 0.40


def test_hedge_level_fails_closed_on_bad_price():
    result = compute_asymmetric_hedge_level(leg1_fill_price=1.5)
    assert result.price is None


# ── _determine_leg1_leader_from_ticks ──────────────────────────────────────

def _t(seconds, yes=None, no=None):
    base = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    return {"created_time": base + timedelta(seconds=seconds), "yes_price": yes, "no_price": no}


def test_ticks_determine_yes_leader_first():
    ticks = [
        _t(0, yes=0.40, no=0.40),
        _t(10, yes=0.55, no=0.40),   # yes crosses 0.48 here
        _t(20, yes=0.50, no=0.55),   # no crosses 0.48 here, later
    ]
    leader, _ = cycle._determine_leg1_leader_from_ticks(ticks, level=0.48)
    assert leader == "yes"


def test_ticks_determine_no_leader_first():
    ticks = [
        _t(0, yes=0.40, no=0.40),
        _t(10, yes=0.40, no=0.55),   # no crosses 0.48 here
        _t(20, yes=0.55, no=0.50),   # yes crosses 0.48 here, later
    ]
    leader, _ = cycle._determine_leg1_leader_from_ticks(ticks, level=0.48)
    assert leader == "no"


def test_ticks_no_crossing_returns_none():
    ticks = [_t(0, yes=0.40, no=0.40)]
    leader, reason = cycle._determine_leg1_leader_from_ticks(ticks, level=0.48)
    assert leader is None
    assert "neither side" in reason


def test_ticks_empty_returns_none():
    leader, reason = cycle._determine_leg1_leader_from_ticks([], level=0.48)
    assert leader is None
    assert "no ticks" in reason


def test_ticks_genuine_tie_returns_none():
    same_t = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    ticks = [{"created_time": same_t, "yes_price": 0.49, "no_price": 0.49}]
    leader, reason = cycle._determine_leg1_leader_from_ticks(ticks, level=0.48)
    assert leader is None
    assert "tie" in reason


# ── Sub-strategy A pass ─────────────────────────────────────────────────────

def _event(ext_id, yes_price, indicators=None, platform="kalshi", category="crypto_15m"):
    return {
        "external_id": ext_id, "yes_price": yes_price, "platform": platform,
        "category": category, "resolution_date": None,
        "best_bid": None, "best_ask": None, "title": None,
        "indicators": indicators or {},
    }


_LATERAL_IND = {"momentum_zscore": 0.1, "stoch": (50, 50), "price_direction": 1.0, "seconds_remaining": 600}
_TRENDING_IND = {"momentum_zscore": 2.0, "stoch": (80, 70), "price_direction": 1.0, "seconds_remaining": 600}


def test_a_admits_candidate_only_in_lateral_regime():
    w = cycle.get_or_create_wallet("crypto15m-a-w1")
    events = [
        _event("lat-1", 0.50, indicators=_LATERAL_IND),
        _event("trend-1", 0.50, indicators=_TRENDING_IND),
    ]
    stats = cycle._run_strategy1_crypto15m_a_pass(w, events, tick_provider=lambda t: [])
    assert stats["crypto15m_a_new_pending"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-lat-1")
    assert bet.status == "crypto15m_A_leg1_pending"
    assert SimulatedBet.select().where(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-trend-1").count() == 0


def test_a_simultaneous_cross_with_no_ticks_is_indeterminate():
    w = cycle.get_or_create_wallet("crypto15m-a-w2")
    cycle._run_strategy1_crypto15m_a_pass(w, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    balance_before = w.balance
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_crypto15m_a_pass(
        w2, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: []
    )
    # yes_ask/no_ask both 0.50 at level 0.48 -> BOTH cross simultaneously with
    # empty ticks (inconclusive) -> should be indeterminate, not a fill.
    assert stats["crypto15m_a_indeterminate"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt")
    assert bet.status == "crypto15m_A_indeterminate"


def test_a_disambiguates_simultaneous_cross_via_ticks():
    w = cycle.get_or_create_wallet("crypto15m-a-w3")
    cycle._run_strategy1_crypto15m_a_pass(w, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    ticks = [_t(0, yes=0.40, no=0.40), _t(5, yes=0.55, no=0.40), _t(10, yes=0.50, no=0.55)]
    stats = cycle._run_strategy1_crypto15m_a_pass(
        w2, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: ticks
    )
    assert stats["crypto15m_a_leg1_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt")
    assert bet.status == "crypto15m_A_leg1_filled_awaiting_remontada"
    meta = json.loads(bet.resolution_meta)
    assert meta["leader"] == "yes"


def test_a_remontada_resolves_arb():
    w = cycle.get_or_create_wallet("crypto15m-a-w4")
    cycle._run_strategy1_crypto15m_a_pass(w, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    # yes crosses alone this cycle -> leg1 filled leader=yes
    cycle._run_strategy1_crypto15m_a_pass(w2, [_event("evt", 0.60, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt")
    assert bet.status == "crypto15m_A_leg1_filled_awaiting_remontada"

    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    # now no_ask (=1-yes_ask) crosses level too -> remontada
    stats = cycle._run_strategy1_crypto15m_a_pass(w3, [_event("evt", 0.40, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    assert stats["crypto15m_a_resolved_arb"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt")
    assert bet.status == "crypto15m_A_resolved_arb"


def test_a_event_disappears_before_any_fill_is_indeterminate():
    w = cycle.get_or_create_wallet("crypto15m-a-w5")
    cycle._run_strategy1_crypto15m_a_pass(w, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_crypto15m_a_pass(w2, [], tick_provider=lambda t: [])
    assert stats["crypto15m_a_indeterminate"] == 1


def test_a_resolves_directional_when_market_closes_without_remontada():
    w = cycle.get_or_create_wallet("crypto15m-a-w6")
    cycle._run_strategy1_crypto15m_a_pass(w, [_event("evt", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_crypto15m_a_pass(w2, [_event("evt", 0.60, indicators=_LATERAL_IND)], tick_provider=lambda t: [])
    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    closed_ind = {**_LATERAL_IND, "seconds_remaining": 0}
    stats = cycle._run_strategy1_crypto15m_a_pass(w3, [_event("evt", 0.70, indicators=closed_ind)], tick_provider=lambda t: [])
    assert stats["crypto15m_a_resolved_directional"] == 1


# ── Sub-strategy B pass ─────────────────────────────────────────────────────

def test_b_admits_market_fill_only_in_trending_regime():
    w = cycle.get_or_create_wallet("crypto15m-b-w1")
    events = [
        _event("trend-1", 0.70, indicators=_TRENDING_IND),
        _event("lat-1", 0.50, indicators=_LATERAL_IND),
    ]
    stats = cycle._run_strategy1_crypto15m_b_pass(w, events)
    assert stats["crypto15m_b_new_market_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mB-trend-1")
    assert bet.status == "crypto15m_B_awaiting_asymmetric_hedge"
    meta = json.loads(bet.resolution_meta)
    assert meta["leader"] == "yes"
    assert meta["hedge_source"] == "fallback_fixed_markup"


def test_b_hedge_fill_resolves_arb():
    w = cycle.get_or_create_wallet("crypto15m-b-w2")
    cycle._run_strategy1_crypto15m_b_pass(w, [_event("evt", 0.70, indicators=_TRENDING_IND)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mB-evt")
    meta = json.loads(bet.resolution_meta)
    hedge_price = meta["hedge_price"]
    assert hedge_price is not None

    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    # loser (no) price now must drop to/below hedge_price. no_ask = 1-yes_ask.
    yes_ask_needed = 1.0 - hedge_price
    stats = cycle._run_strategy1_crypto15m_b_pass(w2, [_event("evt", yes_ask_needed, indicators=_TRENDING_IND)])
    assert stats["crypto15m_b_resolved_arb"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mB-evt")
    assert bet.status == "crypto15m_B_resolved_arb"


def test_b_timeout_resolves_directional():
    w = cycle.get_or_create_wallet("crypto15m-b-w3")
    cycle._run_strategy1_crypto15m_b_pass(w, [_event("evt", 0.70, indicators=_TRENDING_IND)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    near_close_ind = {**_TRENDING_IND, "seconds_remaining": 10}
    # yes_price stays high enough that the loser (no) side does NOT reach the
    # hedge level yet -- isolates the timeout branch from the hedge-fill branch.
    stats = cycle._run_strategy1_crypto15m_b_pass(w2, [_event("evt", 0.60, indicators=near_close_ind)])
    assert stats["crypto15m_b_resolved_directional"] == 1


def test_b_event_disappears_resolves_directional():
    w = cycle.get_or_create_wallet("crypto15m-b-w4")
    cycle._run_strategy1_crypto15m_b_pass(w, [_event("evt", 0.70, indicators=_TRENDING_IND)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_crypto15m_b_pass(w2, [])
    assert stats["crypto15m_b_resolved_directional"] == 1


# ── Wallet-reserve fail-closed guards (real bug 2026-07-16, see
# _available_capital_for_new_stake docstring in paper_trading_cycle.py) ────

def test_crypto15m_a_new_candidate_not_admitted_when_reserve_leaves_zero_available():
    w = SimulatedWallet.create(
        label="crypto15m-a-reserve-1", platform="paper", initial_balance=200.0,
        balance=15.0, equity=200.0, started_at=cycle._now_iso(),
        strategy_scope="all", reset_count=0, is_active=True,
    )
    stats = cycle._run_strategy1_crypto15m_a_pass(
        w, [_event("evt-reserve", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: []
    )
    assert stats["crypto15m_a_new_pending"] == 0
    assert SimulatedBet.select().count() == 0


def test_crypto15m_a_leg1_fill_rejected_fail_closed_when_balance_drained_in_interim():
    w = cycle.get_or_create_wallet("crypto15m-a-reserve-2")
    cycle._run_strategy1_crypto15m_a_pass(
        w, [_event("evt-drain", 0.50, indicators=_LATERAL_IND)], tick_provider=lambda t: []
    )
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt-drain")
    meta = json.loads(bet.resolution_meta)
    stake_usd = meta["stake_usd"]
    assert stake_usd > 0

    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    drained_balance = stake_usd / 2.0
    w2.balance = drained_balance
    w2.save()

    stats = cycle._run_strategy1_crypto15m_a_pass(
        w2, [_event("evt-drain", 0.60, indicators=_LATERAL_IND)], tick_provider=lambda t: []
    )
    assert stats["skipped_insufficient_capital"] == 1
    bet_after = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-crypto15mA-evt-drain")
    assert bet_after.status == "crypto15m_A_insufficient_capital"
    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    assert w3.balance == pytest.approx(drained_balance)
