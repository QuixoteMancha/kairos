"""Tests de las métricas de riesgo-ajustado adicionales (2026-08-04,
pedido explícito del usuario: "investiga qué otras métricas podríamos
usar") -- CAGR, Sharpe/Sortino anualizados sobre la curva de equity
diaria, Calmar ratio, CVaR 95%."""

from __future__ import annotations

import pytest

from scripts.tennis.favorite_by_historical_rank_backtest import (
    _daily_returns, _annualized_sharpe, _annualized_sortino, _cagr_pct,
    _calmar_ratio, _cvar_95_pct, _TRADING_DAYS_PER_YEAR_ATP,
)


def test_daily_returns_computes_pct_change_including_first_day():
    equity = [110.0, 121.0, 108.9]  # +10%, +10%, -10%
    returns = _daily_returns(equity, initial_capital=100.0)
    assert returns == pytest.approx([0.1, 0.1, -0.1])


def test_daily_returns_empty_curve_returns_empty():
    assert _daily_returns([], initial_capital=100.0) == []


def test_annualized_sharpe_none_with_fewer_than_2_points():
    assert _annualized_sharpe([110.0], initial_capital=100.0) is None
    assert _annualized_sharpe([], initial_capital=100.0) is None


def test_annualized_sharpe_positive_for_consistent_growth():
    # crecimiento diario constante del 1% -- stdev no es 0 exacto por
    # redondeo de floats, pero el Sharpe debe ser muy alto (retornos
    # casi idénticos, casi sin varianza real).
    equity = [100.0]
    for _ in range(30):
        equity.append(equity[-1] * 1.01)
    sharpe = _annualized_sharpe(equity, initial_capital=100.0)
    assert sharpe is not None
    assert sharpe > 0


def test_annualized_sortino_none_when_no_downside():
    # todos los días positivos -- Sortino indefinido (no hay downside a medir)
    equity = [110.0, 121.0, 133.1]
    assert _annualized_sortino(equity, initial_capital=100.0) is None


def test_annualized_sortino_penalizes_only_negative_days():
    equity = [110.0, 90.0, 120.0]  # +10%, -18.18%, +33.33%
    sortino = _annualized_sortino(equity, initial_capital=100.0)
    assert sortino is not None


def test_cagr_pct_matches_known_compound_growth():
    # capital x2 en exactamente _TRADING_DAYS_PER_YEAR_ATP días -> CAGR = 100%
    days = round(_TRADING_DAYS_PER_YEAR_ATP)
    equity = [100.0] * (days - 1) + [200.0]
    cagr = _cagr_pct(equity, initial_capital=100.0)
    assert cagr is not None
    assert abs(cagr - 100.0) < 5.0  # tolerancia por el redondeo de `days`


def test_cagr_pct_none_when_capital_busted():
    assert _cagr_pct([0.0], initial_capital=100.0) is None
    assert _cagr_pct([], initial_capital=100.0) is None


def test_calmar_ratio_divides_cagr_by_drawdown():
    assert _calmar_ratio(cagr_pct=20.0, max_drawdown_pct=10.0) == 2.0
    assert _calmar_ratio(cagr_pct=20.0, max_drawdown_pct=None) is None
    assert _calmar_ratio(cagr_pct=None, max_drawdown_pct=10.0) is None
    assert _calmar_ratio(cagr_pct=20.0, max_drawdown_pct=0.0) is None


def test_cvar_95_averages_worst_5pct_of_returns():
    # 100 retornos: 95 ganan +0.1, 5 pierden -1.0 (el peor 5%)
    returns = [0.1] * 95 + [-1.0] * 5
    cvar = _cvar_95_pct(returns)
    assert cvar == -100.0  # promedio del peor 5% = -1.0 -> -100%


def test_cvar_95_none_for_empty_returns():
    assert _cvar_95_pct([]) is None
