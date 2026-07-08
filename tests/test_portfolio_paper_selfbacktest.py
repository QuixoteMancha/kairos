"""Tests for the paper-trading-driven self-backtest source (portfolio.py) and
the Strategy 1 tier B/C calibration gate — added 2026-07-08.

Covers:
  (a) compute_paper_self_backtest() computing real n_bets/roi/win_rate/
      max_dd_pct/sharpe from SimulatedBet history (not BacktestEngine).
  (b) the tier B/C gate: build_portfolio() must classify an uncalibrated
      category's Strategy 1 profile as "directional_uncalibrated" (capped,
      excluded from the 2%/day hard floor) fail-closed, even with a strong
      historical ROI, unless the category is explicitly whitelisted in
      STRATEGY1_CALIBRATED_CATEGORIES.
"""
from __future__ import annotations

import json

import pytest

from src.database.database import AppConfig, init_db
from src.database.models.paper_wallet import SimulatedBet, SimulatedLedger, SimulatedWallet
from src.core.orchestration import portfolio


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def _wallet():
    return SimulatedWallet.create(label="pf-test", platform="paper",
                                   balance=200.0, equity=200.0, initial_balance=200.0)


def _make_bet(wallet, strategy, status, stake, pnl, category=None, roi_pct=0.0, bet_id=None):
    meta = {"category": category} if category else {}
    return SimulatedBet.create(
        bet_id=bet_id or f"bt-{strategy}-{status}-{stake}-{pnl}",
        wallet=wallet, strategy=strategy, total_staked=stake, pnl=pnl,
        expected_roi_pct=roi_pct, status=status,
        resolution_meta=json.dumps(meta),
    )


# ── (a) self-backtest from SimulatedBet ─────────────────────────────────

def test_compute_paper_self_backtest_arb_strategy():
    w = _wallet()
    # 3 wins, 1 loss for strategy_2_cross_platform
    _make_bet(w, "strategy_2_cross_platform", "settled_win", 10.0, 0.5, roi_pct=5.0)
    _make_bet(w, "strategy_2_cross_platform", "settled_win", 10.0, 0.6, roi_pct=6.0)
    _make_bet(w, "strategy_2_cross_platform", "settled_win", 10.0, 0.4, roi_pct=4.0)
    _make_bet(w, "strategy_2_cross_platform", "settled_loss", 10.0, -10.0, roi_pct=0.0)
    # open (non-terminal) bet must be excluded entirely
    _make_bet(w, "strategy_2_cross_platform", "open", 10.0, 0.0, roi_pct=5.0)

    data = portfolio.compute_paper_self_backtest("strategy_2_cross_platform")
    assert data["n_bets"] == 4
    assert data["win_rate"] == pytest.approx(0.75, abs=1e-6)
    # roi_mean = mean of [0.05, 0.06, 0.04, -1.0] = -0.2125
    assert data["roi"] == pytest.approx(-0.2125, abs=1e-4)
    assert data["max_dd_pct"] > 0


def test_compute_paper_self_backtest_empty_is_fail_closed():
    data = portfolio.compute_paper_self_backtest("strategy_3_same_market")
    assert data == {"n_bets": 0, "roi": 0.0, "win_rate": 0.0, "max_dd_pct": 0.0,
                     "sharpe": 0.0, "avg_odds": 0.0}


def test_compute_paper_self_backtest_strategy1_by_category():
    w = _wallet()
    _make_bet(w, "strategy_1_universal", "strategy1_resolved_arb", 10.0, 1.0, category="tennis")
    _make_bet(w, "strategy_1_universal", "strategy1_resolved_directional_loss", 10.0, -10.0, category="tennis")
    _make_bet(w, "strategy_1_universal", "strategy1_resolved_directional_win", 10.0, 2.0, category="football")
    # indeterminate (refunded) must be excluded — no directional outcome
    _make_bet(w, "strategy_1_universal", "strategy1_indeterminate", 10.0, 0.0, category="tennis")

    tennis = portfolio.compute_paper_self_backtest("strategy_1_universal", category="tennis")
    football = portfolio.compute_paper_self_backtest("strategy_1_universal", category="football")
    assert tennis["n_bets"] == 2
    assert football["n_bets"] == 1
    assert football["win_rate"] == 1.0


def test_run_paper_self_backtests_persists_appconfig():
    w = _wallet()
    _make_bet(w, "football_3way", "settled_win", 10.0, 1.0, roi_pct=10.0)
    summary = portfolio.run_paper_self_backtests()
    assert "football_3way" in summary
    stored = json.loads(AppConfig.get_value("self_backtest_football_3way", ""))
    assert stored["n_bets"] == 1
    assert "strategy_1_universal_by_category" in summary
    # every category key present even with zero data (documented gaps, not silent)
    for cat in portfolio.STRATEGY1_CATEGORIES:
        assert cat in summary["strategy_1_universal_by_category"]


# ── (b) tier B/C fail-closed gate ───────────────────────────────────────

def test_strategy1_uncalibrated_category_capped_tier_c():
    # Strong historical ROI for an uncalibrated category (e.g. "baseball")
    # must NOT unlock the hard 2% floor or a large weight — fail-closed.
    assert "baseball" not in portfolio.STRATEGY1_CALIBRATED_CATEGORIES
    AppConfig.set_value(
        "self_backtest_strategy_1_universal_baseball",
        json.dumps({"n_bets": 200, "roi": 0.15, "win_rate": 0.65,
                    "max_dd_pct": 5.0, "sharpe": 2.0, "avg_odds": 2.0}),
    )
    profiles = portfolio._load_strategy1_category_profiles()
    baseball = next(p for p in profiles if p.name == "strategy_1_universal:baseball")
    assert baseball.bucket == "directional_uncalibrated"

    plan = portfolio.build_portfolio(target_daily_roi=0.01)
    w = plan.weights.get("strategy_1_universal:baseball", 0.0)
    assert w <= portfolio.TIER_C_MAX_WEIGHT + 1e-9
    # tier C must never count toward the hard 2%/day floor computation
    assert plan.expected_daily_roi_hard_floor <= plan.expected_daily_roi + 1e-9


def test_strategy1_calibrated_category_gets_tier_b_bucket(monkeypatch):
    # Simulate a category being explicitly whitelisted as calibrated —
    # must then classify as tier B (directional_calibrated), still capped
    # below a low_risk_arb-level weight.
    monkeypatch.setattr(portfolio, "STRATEGY1_CALIBRATED_CATEGORIES", {"weather"})
    AppConfig.set_value(
        "self_backtest_strategy_1_universal_weather",
        json.dumps({"n_bets": 80, "roi": 0.03, "win_rate": 0.55,
                    "max_dd_pct": 8.0, "sharpe": 1.0, "avg_odds": 1.8}),
    )
    profiles = portfolio._load_strategy1_category_profiles()
    weather = next(p for p in profiles if p.name == "strategy_1_universal:weather")
    assert weather.bucket == "directional_calibrated"
