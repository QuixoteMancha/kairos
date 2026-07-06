"""Tests for the universal Strategy 1 state machine wired into
paper_trading_cycle.py (_run_strategy1_pass + _resolve_strategy1_directional).
Uses injected raw events (no real network call) across multiple simulated
cycles to exercise the full observing -> awaiting_hedge -> resolved lifecycle."""
from __future__ import annotations

import json

import pytest

from src.database.database import init_db
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


def _event(ext_id="evt-1", yes_price=0.50, platform="polymarket", resolution_date=None, category="tennis",
           best_bid=None, best_ask=None):
    """NOTE: for platform="polymarket" with no best_bid/best_ask given, the
    naive yes_price/(1-yes_price) complement is used (same fallback the real
    code uses for Kalshi/Manifold) — this is mathematically INCAPABLE of ever
    producing a viable hedge (imp is always exactly 1.0 at any snapshot), so
    any test that needs a genuinely fillable hedge scenario MUST pass
    best_bid/best_ask explicitly to reflect a real (non-complementary) spread."""
    return {
        "external_id": ext_id, "yes_price": yes_price, "platform": platform,
        "category": category, "resolution_date": resolution_date,
        "best_bid": best_bid, "best_ask": best_ask,
    }


def test_candidate_admitted_only_within_band():
    w = cycle.get_or_create_wallet("s1-wallet-1")
    events = [_event("in-band", 0.52), _event("out-of-band", 0.75)]
    stats = cycle._run_strategy1_pass(w, events)
    assert stats["strategy1_new_observing"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-in-band")
    assert bet.status == "strategy1_observing"
    assert SimulatedBet.select().where(SimulatedBet.bet_id == "paper-strategy1-out-of-band").count() == 0


def test_respects_max_new_per_cycle_cap():
    w = cycle.get_or_create_wallet("s1-wallet-2")
    events = [_event(f"evt-{i}", 0.50 + i * 0.001) for i in range(30)]
    stats = cycle._run_strategy1_pass(w, events)
    assert stats["strategy1_new_observing"] == cycle._S1_MAX_NEW_PER_CYCLE


def test_single_leg_fill_deducts_capital_and_moves_to_awaiting_hedge():
    w = cycle.get_or_create_wallet("s1-wallet-3")
    # Cycle 1: admit candidate at 0.50 -> level_a = 0.53, level_b = 0.53 (epsilon 0.03)
    cycle._run_strategy1_pass(w, [_event("evt-fill", 0.50)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-fill")
    meta = json.loads(bet.resolution_meta)
    assert meta["level_a"] == pytest.approx(0.53)

    # Cycle 2: yes_price jumps to 0.60 -> crosses level_a (0.53), no_price=0.40 doesn't cross level_b
    balance_before = w.balance
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_pass(w2, [_event("evt-fill", 0.60)])
    assert stats["strategy1_leg1_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-fill")
    assert bet.status == "strategy1_awaiting_hedge"
    meta = json.loads(bet.resolution_meta)
    assert meta["leader"] == "yes"
    assert meta["l1_price"] == pytest.approx(0.60)

    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    assert w3.balance < balance_before  # capital committed to the directional leg


def test_both_legs_cross_same_cycle_is_indeterminate():
    w = cycle.get_or_create_wallet("s1-wallet-4")
    cycle._run_strategy1_pass(w, [_event("evt-both", 0.50)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    # yes_price=0.99 -> crosses level_a AND no_price=0.01... no_price wouldn't cross level_b (0.53)
    # Force a genuine both-cross: level_a=0.53 crossed by yes=0.60, level_b=0.53 crossed needs no_price>=0.53 i.e. yes<=0.47
    # Use a big single-cycle jump isn't enough to cross both simultaneously (mutually exclusive by construction
    # unless yes_price swings wildly between 2 separate observations we can't capture here) -- instead directly
    # craft the scenario via meta injection to test the guard logic in isolation.
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-both")
    meta = json.loads(bet.resolution_meta)
    meta["level_a"] = 0.55
    meta["level_b"] = 0.55
    bet.resolution_meta = json.dumps(meta)
    bet.save()
    # yes_price=0.60 -> crosses level_a (0.55); no_price=0.40 -> does NOT cross level_b (0.55).
    # To genuinely trigger both, pick yes_price such that no_price=1-yes_price also >= 0.55 -> yes_price <= 0.45,
    # which contradicts crossing level_a (needs yes_price >= 0.55). So "both cross" requires level_a+level_b <= 1,
    # e.g. both at 0.45: yes=0.50 crosses level_a(0.45) and no=0.50 crosses level_b(0.45).
    meta["level_a"] = 0.45
    meta["level_b"] = 0.45
    bet.resolution_meta = json.dumps(meta)
    bet.save()
    stats = cycle._run_strategy1_pass(w2, [_event("evt-both", 0.50)])
    assert stats["strategy1_indeterminate"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-both")
    assert bet.status == "strategy1_indeterminate"


def test_naive_complement_fallback_can_never_produce_a_viable_hedge():
    """Documents the structural math fact (see _yes_no_ask docstring): with
    no best_bid/best_ask (Kalshi/Manifold, or Polymarket without book data),
    yes/no are always exactly complementary at any snapshot, so
    propose_hedge_order() can never find a viable price at the crossing
    instant. This is expected, not a bug — real arb windows need the actual
    book spread (best_bid/best_ask), same gap same_market_arb.py already
    documented for Strategy 3."""
    w = cycle.get_or_create_wallet("s1-wallet-5b")
    cycle._run_strategy1_pass(w, [_event("evt-noarb", 0.50, platform="kalshi")])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-noarb", 0.64, platform="kalshi")])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-noarb")
    assert bet.status == "strategy1_awaiting_hedge"
    meta = json.loads(bet.resolution_meta)
    assert meta["hedge_price"] is None  # structurally unfillable, as expected


def test_hedge_fills_settles_as_arb_with_positive_pnl():
    # Real (non-complementary) book spread via best_bid/best_ask, same fix
    # same_market_arb.py already applies for Strategy 3 — this is what makes
    # a genuine (temporary) arb window mathematically possible at all.
    w = cycle.get_or_create_wallet("s1-wallet-5")
    cycle._run_strategy1_pass(w, [_event("evt-arb", 0.50, best_bid=0.49, best_ask=0.50)])

    # Cycle 2: leader ("yes") crosses and fills at L1=best_ask=0.64. At THIS
    # instant the loser's ask (no_ask=1-0.63=0.37) is still above
    # l2_max=1/1.02-0.64=0.3404 -> hedge not yet viable (hedge_price stays None).
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-arb", 0.64, best_bid=0.63, best_ask=0.64)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-arb")
    meta = json.loads(bet.resolution_meta)
    assert bet.status == "strategy1_awaiting_hedge"
    assert meta["hedge_price"] is None

    # Cycle 3: leader consolidates further (bid/ask both rise toward near-
    # certainty) -> no_ask=1-0.85=0.15, now below l2_max -> hedge gets
    # (re)proposed this cycle at price ~0.245 (interpolated toward l2_max).
    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w3, [_event("evt-arb", 0.90, best_bid=0.85, best_ask=0.91)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-arb")
    meta = json.loads(bet.resolution_meta)
    hedge_price = meta["hedge_price"]
    assert hedge_price is not None
    assert bet.status == "strategy1_awaiting_hedge"  # proposed, not yet filled this same cycle

    # Cycle 4: loser's ask is still at/below the now-fixed hedge price -> fills.
    w4 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_pass(w4, [_event("evt-arb", 0.90, best_bid=0.85, best_ask=0.91)])
    assert stats["strategy1_arb_settled"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-arb")
    assert bet.status == "strategy1_resolved_arb"
    assert bet.pnl > 0


def test_event_disappears_before_any_fill_is_indeterminate_no_capital_moved():
    w = cycle.get_or_create_wallet("s1-wallet-6")
    cycle._run_strategy1_pass(w, [_event("evt-gone", 0.50)])
    balance_before = cycle.SimulatedWallet.get_by_id(w.id).balance
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_pass(w2, [])  # event vanished from feed
    assert stats["strategy1_indeterminate"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-gone")
    assert bet.status == "strategy1_indeterminate"
    assert cycle.SimulatedWallet.get_by_id(w.id).balance == balance_before  # no capital ever committed


def test_directional_resolution_win_and_loss_heuristics():
    w = cycle.get_or_create_wallet("s1-wallet-7")
    cycle._run_strategy1_pass(w, [_event("evt-win", 0.50), _event("evt-loss", 0.50)])

    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-win", 0.90), _event("evt-loss", 0.90)])
    # both now awaiting_hedge with leader "yes" at L1=0.90 (high enough hedge_price may be None
    # since l2_max = 1/1.02 - 0.90 = 0.0804, hedge won't fill from here in this synthetic case)

    # evt-win: event disappears with leader price at 0.97 (last observed before this call) -> win
    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w3, [_event("evt-win", 0.97)])  # update last_leader_price to 0.97
    w4 = cycle.SimulatedWallet.get_by_id(w.id)
    stats_win = cycle._run_strategy1_pass(w4, [])  # evt-win and evt-loss both vanish now
    bet_win = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-win")
    assert bet_win.status == "strategy1_resolved_directional_win"
    assert bet_win.pnl > 0
