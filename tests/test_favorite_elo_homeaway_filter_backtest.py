"""Tests del backtest MLB favorito-por-Elo + filtro winrate-por-rol
(2026-08-05, pedido explícito del usuario -- análogo al de tenis)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.baseball.favorite_elo_homeaway_filter_backtest import (
    _build_elo_homeaway_tracker, _elo_update, run_filtered_backtest, _MIN_ELO_GAP,
    _measure_trading_days_per_year,
)


def _game(date, home, away, home_won):
    return {"date": date, "home_team": home, "away_team": away, "home_won": home_won}


def test_elo_update_is_zero_sum_and_favors_winner():
    new_w, new_l = _elo_update(1500.0, 1500.0)
    assert new_w > 1500.0
    assert new_l < 1500.0
    assert round(new_w + new_l, 4) == 3000.0  # suma cero


def test_elo_tracker_is_point_in_time_correct():
    """El winrate por rol de un partido de evaluación NO debe incluir
    resultados FUTUROS."""
    games = [
        _game("2015-01-01", "A", "Z", True),   # warm-up: A gana de local
        _game("2015-02-01", "Z", "C", False),  # warm-up: C gana de visitante (para tener historial real como underdog-away)
        _game("2016-01-01", "A", "C", True),   # eval: A favorito de local, ya tiene 1-0 en su rol
        _game("2016-06-01", "A", "D", True),   # eval, posterior -- no debe afectar el caso de arriba
    ]
    with patch(
        "scripts.baseball.favorite_elo_homeaway_filter_backtest.collect_games_from_db", return_value=games,
    ):
        cases, meta = _build_elo_homeaway_tracker(games)
    case_2016_01 = next(c for c in cases if c["date"] == "2016-01-01")
    assert case_2016_01["favorite_role_winrate"] == 1.0


def test_excludes_case_when_no_role_history_for_either_team():
    games = [_game("2016-01-01", "A", "B", True)]  # sin warm-up
    cases, meta = _build_elo_homeaway_tracker(games)
    assert cases == []
    assert meta["skipped_no_elo_or_role_history"] == 1


def test_min_elo_gap_constant_is_positive():
    assert _MIN_ELO_GAP > 0


def test_run_filtered_backtest_filters_by_elo_gap_and_role_winrate():
    games = [
        # warm-up: A construye Elo alto y buen historial de local
        _game("2015-01-01", "A", "Z", True),
        _game("2015-02-01", "A", "Z", True),
        _game("2015-03-01", "A", "Z", True),
        # warm-up: B (underdog visitante) pierde seguido de visitante
        _game("2015-04-01", "Z", "B", True),
        # warm-up: C (underdog visitante, gap chico) también con historial real
        _game("2015-05-01", "Z", "C", True),
        # eval: A (local, Elo alto) vs B (visitante, Elo bajo) -- gap grande, A mejor winrate de local -> CALIFICA
        _game("2016-01-01", "A", "B", True),
        # eval: A (local) vs C -- Elo de C aún cerca del inicial (sin warm-up de derrotas de A) -- puede no calificar por gap
    ]
    with patch(
        "scripts.baseball.favorite_elo_homeaway_filter_backtest.collect_games_from_db", return_value=games,
    ):
        result = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None, min_elo_gap=0)
    assert result["filtered_cases_n"] >= 1
    assert result["baseline"]["n_cases"] >= result["filtered_cases_n"]


def test_measure_trading_days_per_year_from_real_dates():
    cases = [{"date": "2016-01-01"}, {"date": "2016-04-01"}, {"date": "2017-01-01"}]
    result = _measure_trading_days_per_year(cases)
    assert result > 0


def test_measure_trading_days_per_year_fallback_with_insufficient_dates():
    from scripts.baseball.favorite_elo_homeaway_filter_backtest import _TRADING_DAYS_PER_YEAR_MLB_DEFAULT
    assert _measure_trading_days_per_year([{"date": "2016-01-01"}]) == _TRADING_DAYS_PER_YEAR_MLB_DEFAULT


def test_run_filtered_backtest_result_has_risk_metrics_keys():
    games = [
        _game("2015-01-01", "A", "Z", True),
        _game("2015-02-01", "A", "Z", True),
        _game("2015-04-01", "Z", "B", True),
        _game("2016-01-01", "A", "B", True),
        _game("2016-02-01", "A", "B", True),
    ]
    with patch(
        "scripts.baseball.favorite_elo_homeaway_filter_backtest.collect_games_from_db", return_value=games,
    ):
        result = run_filtered_backtest(initial_capital=50.0, max_stake_usd=None, min_elo_gap=0)
    for key in ("max_drawdown_pct", "cagr_pct", "annualized_sharpe", "annualized_sortino", "calmar_ratio", "cvar_95_pct"):
        assert key in result["baseline"]
        assert key in result["filtered"]
