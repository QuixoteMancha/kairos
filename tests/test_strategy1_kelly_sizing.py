"""Tests for the per-candidate Kelly-fractional sizing cap added 2026-07-17
(explicit user request after a quant review of Strategy 1's sizing system).

Only the overvaluation model variant has a genuine calibrated per-candidate
edge signal (`fair_prob`) available at new-candidate time -- see
`_overvaluation_kelly_cap_fraction`'s docstring in paper_trading_cycle.py.
For the other 3 variants (generic Strategy 1, crypto15m Sub-A, crypto15m
Sub-B), `_price_spread_kelly_cap_fraction` documents a real, checked finding:
no honest per-candidate probability/edge signal exists at candidate-creation
time (the yes/no ask "spread" is structurally <= 0 by construction of
`_yes_no_ask`, not just dormant), so that helper is a documented no-op
(always returns 1.0, i.e. no additional restriction beyond the pre-existing
reserve/preset ceiling). This file tests that finding explicitly, rather
than fabricating a passing "different edge -> different stake" test for
those 3 variants.

Covers the 3 requested guarantees:
  (a) two overvaluation candidates with different `fair_prob` edge get
      different stakes where they used to be identical.
  (b) the Kelly-capped stake NEVER exceeds what the system already computed
      (reserve + liquidity/preset ceiling) -- Kelly can only shrink, never grow.
  (c) the pre-existing 10% capital reserve is still respected exactly as
      before (Kelly only tightens further, never bypasses it).
"""
from __future__ import annotations

import json

import pytest

from src.database.database import init_db, AppConfig
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.core.orchestration import daily_portfolio_rotation as rotation


@pytest.fixture(autouse=True)
def _clean_paper_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    # Same rationale as test_strategy1_cycle_wiring.py: seed an aggressive
    # rotation state so `get_strategy1_stake_scale()` doesn't fail-closed to
    # 0.0 and mask everything this file is actually testing.
    AppConfig.set_value(rotation.ROTATION_STATE_KEY, json.dumps({
        "preset": rotation.PRESET_AGGRESSIVE,
        "caps": rotation.PRESETS[rotation.PRESET_AGGRESSIVE],
    }))
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def _event(ext_id, yes_price=0.50, category="tennis", best_bid=None, best_ask=None,
           platform="kalshi", indicators=None, resolution_date=None):
    return {
        "external_id": ext_id, "yes_price": yes_price, "platform": platform,
        "category": category, "resolution_date": resolution_date,
        "best_bid": best_bid, "best_ask": best_ask, "title": None,
        "indicators": indicators or {},
    }


def _ov_event(ext_id, fair_prob, favorite_price=0.55, underdog_price=0.42):
    return {
        "external_id": ext_id, "platform": "kalshi", "category": "football",
        "title": "A vs B", "favorite_price": favorite_price, "underdog_price": underdog_price,
        "fair_prob": fair_prob, "book_depth_at_level": 1000.0, "avg_volume": 5000.0,
    }


# ── unit tests for the helper functions directly ───────────────────────────

def test_price_spread_kelly_cap_is_a_documented_noop():
    # Honest finding (see docstring in paper_trading_cycle.py): the raw
    # yes/no ask spread is NOT a usable per-candidate edge signal for
    # generic Strategy 1 / crypto15m Sub-A / Sub-B, regardless of inputs --
    # always neutral (1.0), never restricts, never fabricates a coefficient.
    assert cycle._price_spread_kelly_cap_fraction(0.50, 0.50) == 1.0
    assert cycle._price_spread_kelly_cap_fraction(0.45, 0.55) == 1.0
    assert cycle._price_spread_kelly_cap_fraction(0.30, 0.32) == 1.0
    assert cycle._price_spread_kelly_cap_fraction(None, None) == 1.0


def test_overvaluation_kelly_cap_differs_by_edge_size():
    # Same market_price, different fair_prob -> different edge -> different cap.
    small_edge = cycle._overvaluation_kelly_cap_fraction(
        fair_prob=0.58, market_side="favorite", market_price=0.55)
    big_edge = cycle._overvaluation_kelly_cap_fraction(
        fair_prob=0.75, market_side="favorite", market_price=0.55)
    assert 0.0 < small_edge < big_edge <= 1.0


def test_overvaluation_kelly_cap_zero_when_no_real_edge():
    # fair_prob < market_price on the favorite leg -> negative EV -> Kelly
    # correctly refuses (this IS the honest behavior: fair_prob is a real
    # calibrated probability here, unlike the price-spread proxy).
    cap = cycle._overvaluation_kelly_cap_fraction(
        fair_prob=0.50, market_side="favorite", market_price=0.60)
    assert cap == 0.0


# ── (a) different edge -> different stake, where before it was identical ──

def test_overvaluation_different_fair_prob_edge_yields_different_stakes(monkeypatch):
    monkeypatch.setattr(cycle, "OVERVALUATION_MODEL_ENABLED", True)
    w1 = cycle.get_or_create_wallet("kelly-ov-a")
    w2 = cycle.get_or_create_wallet("kelly-ov-b")

    # Both would receive the SAME liquidity-derived stake_usd before Kelly
    # (n * market_price is identical -- same favorite_price/liquidity
    # inputs). fair_prob is the only thing that differs.
    small_edge_event = _ov_event("ov-small-edge", fair_prob=0.58)
    big_edge_event = _ov_event("ov-big-edge", fair_prob=0.80)

    cycle._run_strategy1_overvaluation_pass(w1, [small_edge_event], threshold=0.10)
    cycle._run_strategy1_overvaluation_pass(w2, [big_edge_event], threshold=0.10)

    bet1 = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-ov-small-edge")
    bet2 = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-ov-big-edge")
    stake1 = json.loads(bet1.resolution_meta)["stake_usd"]
    stake2 = json.loads(bet2.resolution_meta)["stake_usd"]

    assert stake1 != stake2
    assert stake1 < stake2


def test_generic_strategy1_stakes_unaffected_by_kelly_noop():
    # Companion to (a): confirms the documented no-op explicitly at the
    # cycle level -- two generic Strategy 1 candidates that would have been
    # sized identically before Kelly (same yes_price, no real spread signal)
    # are STILL sized identically after wiring Kelly in, because
    # `_price_spread_kelly_cap_fraction` always returns 1.0 for this variant
    # (see its docstring: no fabricated differentiation).
    w1 = cycle.get_or_create_wallet("kelly-s1-noop-a")
    w2 = cycle.get_or_create_wallet("kelly-s1-noop-b")
    cycle._run_strategy1_pass(w1, [_event("noop-a", yes_price=0.50)])
    cycle._run_strategy1_pass(w2, [_event("noop-b", yes_price=0.52)])
    bet1 = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-noop-a")
    bet2 = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-noop-b")
    stake1 = json.loads(bet1.resolution_meta)["stake_usd"]
    stake2 = json.loads(bet2.resolution_meta)["stake_usd"]
    # Both wallets start with identical balance/reserve/preset -> identical stake.
    assert stake1 == stake2


# ── (b) Kelly-capped stake never exceeds the pre-existing ceiling ─────────

def test_generic_strategy1_kelly_cap_never_exceeds_1_0():
    # Direct property check: the no-op cap can never be used to scale a
    # stake UP past the pre-existing reserve/preset ceiling, for any input.
    for yes_ask, no_ask in [(0.50, 0.50), (0.30, 0.32), (0.10, 0.90), (None, None)]:
        assert cycle._price_spread_kelly_cap_fraction(yes_ask, no_ask) <= 1.0


def test_overvaluation_kelly_never_exceeds_liquidity_derived_stake(monkeypatch):
    monkeypatch.setattr(cycle, "OVERVALUATION_MODEL_ENABLED", True)
    w = cycle.get_or_create_wallet("kelly-ov-ceiling")
    event = _ov_event("ov-ceiling", fair_prob=0.95)  # very large edge

    from src.core.strategies.overvaluation_gap import max_order_size_from_liquidity
    n = max_order_size_from_liquidity(book_depth_at_level=1000.0, avg_volume=5000.0)
    liquidity_derived_stake = round(n * 0.55, 6)

    cycle._run_strategy1_overvaluation_pass(w, [event], threshold=0.10)
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-ov-ceiling")
    stake = json.loads(bet.resolution_meta)["stake_usd"]
    assert stake <= liquidity_derived_stake + 1e-6


def test_overvaluation_kelly_reduces_stake_below_ceiling_for_small_edge(monkeypatch):
    # (b)'s complement: confirm Kelly actually DOES shrink the stake (not
    # just "never exceeds", which would trivially pass at cap==1.0 too).
    monkeypatch.setattr(cycle, "OVERVALUATION_MODEL_ENABLED", True)
    w = cycle.get_or_create_wallet("kelly-ov-shrink")
    event = _ov_event("ov-shrink", fair_prob=0.58)  # small but real edge

    from src.core.strategies.overvaluation_gap import max_order_size_from_liquidity
    n = max_order_size_from_liquidity(book_depth_at_level=1000.0, avg_volume=5000.0)
    liquidity_derived_stake = round(n * 0.55, 6)

    cycle._run_strategy1_overvaluation_pass(w, [event], threshold=0.10)
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-ov-shrink")
    stake = json.loads(bet.resolution_meta)["stake_usd"]
    assert stake < liquidity_derived_stake


# ── (c) 10% reserve still respected exactly as before ─────────────────────

def test_10pct_reserve_still_respected_with_kelly_active():
    w = cycle.get_or_create_wallet("kelly-reserve-check")
    wallet = SimulatedWallet.get(SimulatedWallet.label == "kelly-reserve-check")
    available = cycle._available_capital_for_new_stake(wallet)
    # Reserve math is untouched: available capital must be exactly 90% of balance.
    assert available == pytest.approx(wallet.balance * 0.90, abs=1e-6)

    cycle._run_strategy1_pass(w, [_event("reserve-check", yes_price=0.50)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-reserve-check")
    stake = json.loads(bet.resolution_meta)["stake_usd"]
    assert stake <= available


def test_10pct_reserve_still_respected_overvaluation(monkeypatch):
    monkeypatch.setattr(cycle, "OVERVALUATION_MODEL_ENABLED", True)
    w = cycle.get_or_create_wallet("kelly-ov-reserve-check")
    wallet = SimulatedWallet.get(SimulatedWallet.label == "kelly-ov-reserve-check")
    available_before = cycle._available_capital_for_new_stake(wallet)
    assert available_before == pytest.approx(wallet.balance * 0.90, abs=1e-6)

    event = _ov_event("ov-reserve-check", fair_prob=0.90)
    cycle._run_strategy1_overvaluation_pass(w, [event], threshold=0.10)
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-ov-reserve-check")
    stake = json.loads(bet.resolution_meta)["stake_usd"]
    assert stake <= available_before
