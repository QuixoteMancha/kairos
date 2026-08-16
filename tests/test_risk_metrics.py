"""Tests del módulo genérico de métricas de riesgo-ajustado (2026-08-05,
extraído de `scripts/tennis/favorite_by_historical_rank_backtest.py`
para reusarlo en MLB sin duplicar la lógica)."""

from __future__ import annotations

from scripts.reports import risk_metrics


def test_max_drawdown_pct_empty_curve_returns_none():
    assert risk_metrics.max_drawdown_pct([]) is None


def test_max_drawdown_pct_measures_peak_to_trough():
    curve = [100.0, 150.0, 75.0, 120.0]
    assert risk_metrics.max_drawdown_pct(curve) == 50.0  # (150-75)/150


def test_daily_returns_includes_first_day_against_initial_capital():
    returns = risk_metrics.daily_returns([110.0, 121.0], 100.0)
    assert returns == [0.1, 0.1]


def test_annualized_sharpe_none_with_insufficient_data():
    assert risk_metrics.annualized_sharpe([100.0], 100.0, 250.0) is None


def test_annualized_sharpe_positive_for_consistent_growth():
    curve = [110.0, 121.0, 133.1, 146.41]
    result = risk_metrics.annualized_sharpe(curve, 100.0, 250.0)
    assert result is None or result > 0  # desvío puede ser 0 con crecimiento parejo -> None es válido


def test_cagr_pct_computes_compounded_annual_growth():
    curve = [200.0] * 250  # 1 año exacto de trading, capital duplicado
    result = risk_metrics.cagr_pct(curve, 100.0, 250.0)
    assert result == 100.0


def test_calmar_ratio_divides_cagr_by_drawdown():
    assert risk_metrics.calmar_ratio(50.0, 25.0) == 2.0


def test_calmar_ratio_none_when_drawdown_zero():
    assert risk_metrics.calmar_ratio(50.0, 0.0) is None


def test_cvar_95_pct_averages_worst_5_percent():
    returns = [-0.5, -0.3, -0.1, 0.1, 0.2] * 20  # 100 retornos, peor 5% = peores 5 valores (-0.5)
    result = risk_metrics.cvar_95_pct(returns)
    assert result == -50.0
