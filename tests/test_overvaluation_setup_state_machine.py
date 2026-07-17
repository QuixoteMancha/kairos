"""Tests for Fase 3 (docs/pre-event-overvaluation-model-plan-2026-07-11.md):
`classify_overvaluation_setup()` (src/core/strategies/overvaluation_gap.py)
and the standalone state machine `_run_strategy1_overvaluation_pass()` in
paper_trading_cycle.py. Neither is wired into `run_paper_trading_cycle()`
yet (Fase 4, out of scope) -- exercised directly with injected fixture
events, same pattern used for the crypto15m A/B passes before their Fase 4
wiring."""
from __future__ import annotations

import json

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.core.strategies.overvaluation_gap import classify_overvaluation_setup


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


@pytest.fixture
def wallet():
    return SimulatedWallet.create(
        label="t-overvaluation", platform="paper", initial_balance=200.0,
        balance=200.0, equity=200.0, started_at=cycle._now_iso(),
        strategy_scope="all", reset_count=0, is_active=True,
    )


# ── classify_overvaluation_setup ────────────────────────────────────────

def test_gap_above_threshold_is_favorite_overvalued():
    assert classify_overvaluation_setup(gap=0.15, threshold=0.10) == "favorite_overvalued"


def test_gap_at_threshold_is_market_justified():
    assert classify_overvaluation_setup(gap=0.10, threshold=0.10) == "market_justified"


def test_negative_gap_is_market_justified():
    assert classify_overvaluation_setup(gap=-0.20, threshold=0.10) == "market_justified"


def test_rejects_negative_threshold():
    with pytest.raises(ValueError):
        classify_overvaluation_setup(gap=0.05, threshold=-0.01)


# ── _run_strategy1_overvaluation_pass: admission ────────────────────────

def _tennis_event(ext_id="tennis-1", favorite_price=0.75, underdog_price=0.30,
                   fair_prob=0.55, book_depth_at_level=1000.0, avg_volume=None,
                   seconds_remaining=None):
    return {
        "external_id": ext_id, "platform": "kalshi", "category": "tennis",
        "title": "Player A vs Player B",
        "favorite_price": favorite_price, "underdog_price": underdog_price,
        "fair_prob": fair_prob,
        "book_depth_at_level": book_depth_at_level, "avg_volume": avg_volume,
        "indicators": {"seconds_remaining": seconds_remaining},
    }


def _football_event(ext_id="football-1", favorite_price=0.55, underdog_price=0.42,
                     fair_prob=0.53, book_depth_at_level=500.0, avg_volume=None,
                     seconds_remaining=None):
    return {
        "external_id": ext_id, "platform": "kalshi", "category": "football",
        "title": "Team A vs Team B",
        "favorite_price": favorite_price, "underdog_price": underdog_price,
        "fair_prob": fair_prob,
        "book_depth_at_level": book_depth_at_level, "avg_volume": avg_volume,
        "indicators": {"seconds_remaining": seconds_remaining},
    }


def test_favorite_overvalued_admits_underdog_market_leg(wallet):
    # gap = 0.75 - 0.55 = 0.20 > threshold 0.10 -> favorite_overvalued
    ev = _tennis_event(favorite_price=0.75, underdog_price=0.30, fair_prob=0.55)
    stats = cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-tennis-1")
    meta = json.loads(bet.resolution_meta)
    assert meta["setup"] == "favorite_overvalued"
    assert meta["market_side"] == "underdog"
    assert meta["hedge_side"] == "favorite"
    assert bet.status == "overvaluation_market_leg_filled_awaiting_hedge"


def test_market_justified_admits_favorite_market_leg(wallet):
    # gap = 0.55 - 0.60 = -0.05 <= threshold 0.10 -> market_justified, AND
    # fair_prob(0.60) > favorite_price(0.55) so the market leg (favorite) has
    # a real positive Kelly edge (2026-07-17, see
    # `_overvaluation_kelly_cap_fraction`) -- with fair_prob=0.53 < 0.55 the
    # favorite would be slightly overvalued even here, a negative-EV buy
    # that Kelly correctly caps to $0 (not a bug in Kelly, the old fixture
    # just didn't encode a real edge).
    ev = _football_event(favorite_price=0.55, underdog_price=0.42, fair_prob=0.60)
    stats = cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-football-1")
    meta = json.loads(bet.resolution_meta)
    assert meta["setup"] == "market_justified"
    assert meta["market_side"] == "favorite"
    assert meta["hedge_side"] == "underdog"


def test_market_leg_price_outside_band_discards_entire_setup_zero_legs(wallet):
    # favorite_overvalued setup, but the market leg (underdog) price is 0.05,
    # outside [0.20, 0.80] -> whole setup discarded, no bet created.
    ev = _tennis_event(favorite_price=0.90, underdog_price=0.05, fair_prob=0.55)
    stats = cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 0
    assert SimulatedBet.select().count() == 0


def test_zero_liquidity_discards_entire_setup_zero_legs(wallet):
    ev = _tennis_event(favorite_price=0.75, underdog_price=0.30, fair_prob=0.55,
                        book_depth_at_level=0.0, avg_volume=None)
    stats = cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 0
    assert SimulatedBet.select().count() == 0


def test_baseball_no_dynamic_epsilon_still_admits_with_fallback(wallet):
    ev = {
        "external_id": "baseball-1", "platform": "kalshi", "category": "baseball",
        "title": "Team X vs Team Y",
        "favorite_price": 0.70, "underdog_price": 0.35, "fair_prob": 0.50,
        "book_depth_at_level": 400.0, "avg_volume": None,
        "indicators": {"seconds_remaining": None},
    }
    stats = cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-baseball-1")
    meta = json.loads(bet.resolution_meta)
    assert meta["dynamic_epsilon_available"] is False


# ── resolution: hedge fill / directional / disappearance ───────────────

def test_resting_hedge_fill_resolves_arb(wallet):
    ev = _tennis_event(favorite_price=0.75, underdog_price=0.30, fair_prob=0.55)
    cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-tennis-1")
    meta = json.loads(bet.resolution_meta)
    hedge_price = meta["hedge_price"]

    ev2 = _tennis_event(favorite_price=hedge_price, underdog_price=0.20, fair_prob=0.55)
    stats2 = cycle._run_strategy1_overvaluation_pass(wallet, [ev2], threshold=0.10)
    assert stats2["overvaluation_resolved_arb"] == 1
    resolved = SimulatedBet.get(SimulatedBet.bet_id == bet.bet_id)
    assert resolved.status == "overvaluation_resolved_arb"


def test_event_disappears_resolves_directional(wallet):
    ev = _tennis_event(favorite_price=0.75, underdog_price=0.30, fair_prob=0.55)
    cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)

    stats2 = cycle._run_strategy1_overvaluation_pass(wallet, [], threshold=0.10)
    assert stats2["overvaluation_resolved_directional"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-tennis-1")
    assert bet.status == "overvaluation_resolved_directional"


def test_event_closes_without_hedge_fill_resolves_directional(wallet):
    ev = _tennis_event(favorite_price=0.75, underdog_price=0.30, fair_prob=0.55)
    cycle._run_strategy1_overvaluation_pass(wallet, [ev], threshold=0.10)

    ev2 = _tennis_event(favorite_price=0.78, underdog_price=0.25, fair_prob=0.55,
                         seconds_remaining=0)
    stats2 = cycle._run_strategy1_overvaluation_pass(wallet, [ev2], threshold=0.10)
    assert stats2["overvaluation_resolved_directional"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-overvaluation-tennis-1")
    assert bet.status == "overvaluation_resolved_directional"
