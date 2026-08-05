"""Tests del backtest combinado ATP+WTA (2026-08-05, pedido explícito
del usuario -- "una corrida combinada atp + wta con la estrategia
seleccionada para atp + la estrategia 1 para wta")."""

from __future__ import annotations

from unittest.mock import patch

from scripts.tennis.combined_atp_wta_backtest import (
    simulate_combined, run_backtest, _build_combined_cases, _net_hold_pct, _net_reprice_pct,
)


def test_net_hold_pct_matches_simple_formula_direction():
    assert _net_hold_pct(0.5, True) > 0
    assert _net_hold_pct(0.5, False) <= -1.0  # -100% del stake, más la fee de entrada


def test_simulate_combined_strategy_1_case_simple_hold():
    cases = [
        {"date": "2016-01-01", "favorite_rank": 5, "underdog_rank": 300, "hit": True,
         "tour": "WTA", "strategy": "1", "price_pre_match": 0.7},
    ]
    result = simulate_combined(cases, initial_capital=100.0, max_stake_usd=None)
    assert result["n_trades"] == 1
    assert result["n_trades_by_tour"] == {"ATP": 0, "WTA": 1}
    assert result["final_capital"] > 100.0  # ganó, precio implica payout positivo


def test_simulate_combined_strategy_8_case_reinforces_on_set1_win():
    cases = [
        {"date": "2016-01-01", "favorite_rank": 5, "underdog_rank": 300, "hit": True,
         "tour": "ATP", "strategy": "8", "price_pre_match": 0.6, "price_after_set1_win": 0.8,
         "price_after_set1_loss": 0.3, "price_at_1_1": 0.7,
         "set1_favorite_win": True, "set2_favorite_win": True},
    ]
    result = simulate_combined(cases, initial_capital=100.0, max_stake_usd=None)
    # pre-match + refuerzo -- 2 resoluciones reales (ambas ganadoras)
    assert result["n_trades"] == 2
    assert result["hit_rate"] == 1.0


def test_simulate_combined_strategy_8_case_exits_reinforcement_leg_on_set2_loss():
    cases = [
        {"date": "2016-01-01", "favorite_rank": 5, "underdog_rank": 300, "hit": True,
         "tour": "ATP", "strategy": "8", "price_pre_match": 0.6, "price_after_set1_win": 0.8,
         "price_after_set1_loss": 0.3, "price_at_1_1": 0.7,
         "set1_favorite_win": True, "set2_favorite_win": False},
    ]
    result = simulate_combined(cases, initial_capital=100.0, max_stake_usd=None)
    # la pata pre-match SÍ resuelve (hit); la de refuerzo se retira
    # (reprecio, no cuenta para hit_rate) -- solo 1 trade resuelto.
    assert result["n_trades"] == 1
    assert result["hit_rate"] == 1.0


def test_simulate_combined_shares_daily_exposure_cap_across_tours():
    # 2 casos el MISMO día, uno de cada tour -- deben competir por el
    # MISMO cupo de exposición (no 2 cupos separados por tour).
    cases = [
        {"date": "2016-01-01", "favorite_rank": 1, "underdog_rank": 300, "hit": True,
         "tour": "ATP", "strategy": "8", "price_pre_match": 0.6, "price_after_set1_win": 0.8,
         "price_after_set1_loss": 0.3, "price_at_1_1": 0.7,
         "set1_favorite_win": False, "set2_favorite_win": None},
        {"date": "2016-01-01", "favorite_rank": 2, "underdog_rank": 300, "hit": True,
         "tour": "WTA", "strategy": "1", "price_pre_match": 0.6},
    ]
    result = simulate_combined(cases, initial_capital=100.0, stake_pct=0.4, max_simultaneous_pct=0.5, max_stake_usd=None)
    # 40% + 40% = 80% > 50% de cupo -- el segundo (peor ranking, WTA) debe quedar afuera.
    assert result["skipped_daily_cap"] == 1


def test_run_backtest_returns_expected_shape():
    with patch(
        "scripts.tennis.combined_atp_wta_backtest._build_combined_cases", return_value=([], {}),
    ):
        result = run_backtest()
    assert "combined" in result
    assert result["combined"]["n_trades"] == 0
