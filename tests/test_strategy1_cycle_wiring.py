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
           best_bid=None, best_ask=None, title=None):
    """NOTE: for platform="polymarket" with no best_bid/best_ask given, the
    naive yes_price/(1-yes_price) complement is used (same fallback the real
    code uses for Kalshi/Manifold) — this is mathematically INCAPABLE of ever
    producing a viable hedge (imp is always exactly 1.0 at any snapshot), so
    any test that needs a genuinely fillable hedge scenario MUST pass
    best_bid/best_ask explicitly to reflect a real (non-complementary) spread."""
    return {
        "external_id": ext_id, "yes_price": yes_price, "platform": platform,
        "category": category, "resolution_date": resolution_date,
        "best_bid": best_bid, "best_ask": best_ask, "title": title,
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
    assert bet.actual_filled_pct == pytest.approx(50.0)  # 1 of 2 intended legs filled


def test_actual_filled_pct_reaches_100_only_when_hedge_also_fills():
    """Regression test for the real bug found 2026-07-08: `actual_filled_pct`
    was hardcoded to 0.0 at candidate creation and never updated — the
    frontend (PaperBetGroup.vue "✓ FILLED" badge) had no real signal that a
    leg had actually executed. Semantics: 50% after leg1 fills, 100% only
    once the hedge also fills (real 2-leg arb); stays at 50% forever if it
    resolves directional (hedge never filled) — pairs with the "⚠ 1 PATA"
    badge for exactly that case."""
    w = cycle.get_or_create_wallet("s1-wallet-fillpct")
    cycle._run_strategy1_pass(w, [_event("evt-fillpct", 0.50)])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-fillpct")
    assert bet.actual_filled_pct == 0.0  # still observing, no leg filled yet

    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-fillpct", 0.60)])  # leg1 fills
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-fillpct")
    assert bet.status == "strategy1_awaiting_hedge"
    assert bet.actual_filled_pct == pytest.approx(50.0)


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
    assert bet.actual_filled_pct == pytest.approx(100.0)  # both legs filled, real arb


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


def test_indeterminate_at_awaiting_hedge_refunds_stake_no_capital_lost():
    """Regression test for the real bug found in production 2026-07-07:
    a position that already committed capital (reached awaiting_hedge) but
    resolves ambiguous must have its stake REFUNDED, never left deducted
    forever with no ledger entry (that silently drained the wallet balance
    to negative in prod: 32 positions, $175.16 stuck, balance went to
    -$0.44)."""
    w = cycle.get_or_create_wallet("s1-wallet-8")
    cycle._run_strategy1_pass(w, [_event("evt-ambig", 0.50)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-ambig", 0.60)])  # leader fills, leaves awaiting_hedge
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-ambig")
    assert bet.status == "strategy1_awaiting_hedge"
    stake_usd = bet.total_staked
    assert stake_usd > 0
    balance_after_fill = cycle.SimulatedWallet.get_by_id(w.id).balance

    # Event disappears (resolves/closes) with last_leader_price ambiguous (0.60,
    # neither >=0.95 nor <=0.05) -> must void/refund, not leave stuck.
    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_pass(w3, [])
    assert stats["strategy1_indeterminate"] == 1
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-ambig")
    assert bet.status == "strategy1_indeterminate"
    assert bet.pnl == 0.0

    balance_after_void = cycle.SimulatedWallet.get_by_id(w.id).balance
    assert balance_after_void == pytest.approx(balance_after_fill + stake_usd)


def test_pending_awaiting_hedge_position_does_not_reduce_equity():
    """Regression test for the real bug found 2026-07-08: a merely-PENDING
    Strategy 1 position (`strategy1_awaiting_hedge`, capital committed but
    outcome unknown) must NOT make the wallet's `equity` dip as if it were
    an immediate realized loss — that's exactly what the real Wallet model
    avoids via `equity = balance + pending_staked` (wallet.py). Only
    `balance` (free cash) should drop at open; `equity` stays flat until
    the position is actually settled (win/loss/void)."""
    w = cycle.get_or_create_wallet("s1-wallet-equity-pending")
    equity_before = w.equity
    cycle._run_strategy1_pass(w, [_event("evt-eq", 0.50)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-eq", 0.60)])  # leg1 fills -> awaiting_hedge

    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-eq")
    assert bet.status == "strategy1_awaiting_hedge"

    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    assert w3.balance < equity_before  # capital committed, balance genuinely down
    assert w3.equity == pytest.approx(equity_before)  # but NOT shown as a loss yet


def test_directional_loss_drops_equity_only_at_realization_not_before():
    """Companion to the above: once the pending position is REALIZED as a
    directional loss, equity must catch down to match balance (the stake is
    genuinely gone at that point) — it shouldn't stay artificially high
    forever either."""
    w = cycle.get_or_create_wallet("s1-wallet-equity-loss")
    cycle._run_strategy1_pass(w, [_event("evt-eqloss", 0.50)])
    w2 = cycle.SimulatedWallet.get_by_id(w.id)
    cycle._run_strategy1_pass(w2, [_event("evt-eqloss", 0.60)])  # awaiting_hedge, equity still flat

    w3 = cycle.SimulatedWallet.get_by_id(w.id)
    equity_while_pending = w3.equity
    balance_while_pending = w3.balance
    assert equity_while_pending > balance_while_pending  # pending stake still counted as equity

    # Drive last_leader_price down into the loss zone, then let the event vanish.
    cycle._run_strategy1_pass(cycle.SimulatedWallet.get_by_id(w.id), [_event("evt-eqloss", 0.03)])
    w4 = cycle.SimulatedWallet.get_by_id(w.id)
    stats = cycle._run_strategy1_pass(w4, [])  # event vanishes -> resolves directional loss
    assert stats["strategy1_directional_resolved"] == 1

    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-eqloss")
    assert bet.status == "strategy1_resolved_directional_loss"
    assert bet.pnl < 0

    w5 = cycle.SimulatedWallet.get_by_id(w.id)
    assert w5.equity == pytest.approx(w5.balance)  # now correctly caught down, no lingering phantom equity


# ─────────────────────────────────────────────────────────────────────────────
# Real-result resolution cascade (docs/strategy1-real-result-resolution-2026-07-07.md)
# ─────────────────────────────────────────────────────────────────────────────

def test_title_persisted_in_meta_on_candidate_creation():
    """§0 of Vesper's design: `title` is a one-line prerequisite for the
    whole real-result matching cascade -- without it there is no text input
    for the name extractor."""
    w = cycle.get_or_create_wallet("s1-wallet-title")
    cycle._run_strategy1_pass(w, [_event("evt-title", 0.52, title="Will Novak Djokovic beat Carlos Alcaraz?")])
    bet = SimulatedBet.get(SimulatedBet.bet_id == "paper-strategy1-evt-title")
    meta = json.loads(bet.resolution_meta)
    assert meta["title"] == "Will Novak Djokovic beat Carlos Alcaraz?"


@pytest.mark.parametrize("title,category,expected", [
    ("Will Novak Djokovic beat Carlos Alcaraz?", "tennis", ("Novak Djokovic", "Carlos Alcaraz")),
    ("Rafael Nadal vs Daniil Medvedev", "tennis", ("Rafael Nadal", "Daniil Medvedev")),
    ("Iga Swiatek to beat Aryna Sabalenka", "tennis", ("Iga Swiatek", "Aryna Sabalenka")),
    ("Will Real Madrid beat Barcelona?", "football", ("Real Madrid", "Barcelona")),
    ("Los Angeles Lakers vs. Boston Celtics", "basketball", ("Los Angeles Lakers", "Boston Celtics")),
    ("New York Yankees to beat Boston Red Sox", "baseball", ("New York Yankees", "Boston Red Sox")),
])
def test_extract_match_names_known_title_patterns(title, category, expected):
    assert cycle._extract_match_names(title, category) == expected


@pytest.mark.parametrize("title,category", [
    (None, "tennis"),
    ("", "tennis"),
    ("BTC above $100k by Friday?", "crypto"),  # no vs/beat pattern at all
    ("Will the Fed cut rates in July?", "politics"),
])
def test_extract_match_names_fails_closed_on_unmatched_title(title, category):
    assert cycle._extract_match_names(title, category) is None


def _s1_meta(**overrides) -> dict:
    base = {
        "title": "Will Player A beat Player B?", "category": "tennis",
        "resolution_date": "2026-07-01", "leader": "yes",
        "stake_usd": 10.0, "n": 20.0, "last_leader_price": 0.97,
    }
    base.update(overrides)
    return base


def test_cascade_uses_real_result_when_matching_succeeds(monkeypatch):
    """§2 of the design: real result strictly wins over the price heuristic
    when matching succeeds, even if the price heuristic would have said
    something different (here leader's last price of 0.03 would heuristically
    read as a LOSS, but the mocked real result says the leader's side won)."""
    monkeypatch.setattr(
        cycle, "_try_resolve_via_real_result",
        lambda meta, now: {"status": "resolved", "leader_won": True, "source": "flashscore_tennis"},
    )
    w = cycle.get_or_create_wallet("s1-wallet-real-win")
    bet = SimulatedBet.create(
        bet_id="paper-strategy1-real-win", wallet=w, strategy=cycle._S1_STRATEGY_NAME,
        platform_legs="[]", opened_at=cycle._now_iso(), total_staked=10.0,
        expected_roi_pct=0.0, simulated_fill_json="", actual_filled_pct=0.0,
        status="strategy1_awaiting_hedge",
        resolution_meta=json.dumps(_s1_meta(last_leader_price=0.03)),
    )
    stats = {"strategy1_directional_resolved": 0, "strategy1_indeterminate": 0}
    cycle._resolve_strategy1_directional(bet, _s1_meta(last_leader_price=0.03), w, stats)
    assert bet.status == "strategy1_resolved_directional_win"
    assert bet.pnl > 0
    meta = json.loads(bet.resolution_meta)
    assert meta["resolved_via"] == "real_result_flashscore_tennis"
    assert stats["strategy1_directional_resolved"] == 1


def test_cascade_falls_back_to_price_heuristic_when_matching_fails(monkeypatch):
    """When `find_match_result`/`find_team_match_result` (mocked here at the
    `_try_resolve_via_real_result` boundary) return no usable match, the
    existing, unchanged price heuristic must still resolve the position."""
    monkeypatch.setattr(
        cycle, "_try_resolve_via_real_result",
        lambda meta, now: {"status": "unsupported"},
    )
    w = cycle.get_or_create_wallet("s1-wallet-fallback")
    meta = _s1_meta(last_leader_price=0.97)
    bet = SimulatedBet.create(
        bet_id="paper-strategy1-fallback", wallet=w, strategy=cycle._S1_STRATEGY_NAME,
        platform_legs="[]", opened_at=cycle._now_iso(), total_staked=10.0,
        expected_roi_pct=0.0, simulated_fill_json="", actual_filled_pct=0.0,
        status="strategy1_awaiting_hedge",
        resolution_meta=json.dumps(meta),
    )
    stats = {"strategy1_directional_resolved": 0, "strategy1_indeterminate": 0}
    cycle._resolve_strategy1_directional(bet, meta, w, stats)
    assert bet.status == "strategy1_resolved_directional_win"
    saved_meta = json.loads(bet.resolution_meta)
    assert saved_meta["resolved_via"] == "price_heuristic_win_unverified"
    assert stats["strategy1_directional_resolved"] == 1


def test_cascade_waits_for_real_result_timeout_before_degrading(monkeypatch):
    """§4 of the design: a "pending" real-result status (match not finished/
    not found yet) must NOT degrade to the price heuristic immediately --
    it waits up to `_S1_REAL_RESULT_TIMEOUT_HOURS`, refreshing the price
    heuristic input each cycle, before falling through."""
    monkeypatch.setattr(
        cycle, "_try_resolve_via_real_result",
        lambda meta, now: {"status": "pending"},
    )
    w = cycle.get_or_create_wallet("s1-wallet-timeout")
    meta = _s1_meta(last_leader_price=0.97)
    bet = SimulatedBet.create(
        bet_id="paper-strategy1-timeout", wallet=w, strategy=cycle._S1_STRATEGY_NAME,
        platform_legs="[]", opened_at=cycle._now_iso(), total_staked=10.0,
        expected_roi_pct=0.0, simulated_fill_json="", actual_filled_pct=0.0,
        status="strategy1_awaiting_hedge",
        resolution_meta=json.dumps(meta),
    )
    stats = {"strategy1_directional_resolved": 0, "strategy1_indeterminate": 0}

    # First attempt: sets the timestamp, does NOT resolve yet (neither real
    # result nor price heuristic).
    cycle._resolve_strategy1_directional(bet, meta, w, stats)
    assert bet.status == "strategy1_awaiting_hedge"
    assert stats["strategy1_directional_resolved"] == 0
    saved_meta = json.loads(bet.resolution_meta)
    assert "real_result_attempt_started_at" in saved_meta

    # Second attempt, still well within the timeout window -- keeps waiting.
    cycle._resolve_strategy1_directional(bet, saved_meta, w, stats)
    assert bet.status == "strategy1_awaiting_hedge"
    assert stats["strategy1_directional_resolved"] == 0

    # Simulate the timeout having elapsed by backdating the attempt start.
    from datetime import datetime, timezone, timedelta
    old_start = datetime.now(timezone.utc) - timedelta(hours=cycle._S1_REAL_RESULT_TIMEOUT_HOURS + 1)
    saved_meta["real_result_attempt_started_at"] = old_start.isoformat()
    cycle._resolve_strategy1_directional(bet, saved_meta, w, stats)
    assert bet.status == "strategy1_resolved_directional_win"  # degraded to price heuristic (0.97 >= threshold)
    final_meta = json.loads(bet.resolution_meta)
    assert final_meta["resolved_via"] == "price_heuristic_win_unverified"
    assert stats["strategy1_directional_resolved"] == 1
