"""
test_portfolio.py — auto-conocimiento riesgo-retorno + factibilidad del target.

Prueba la construcción del portafolio desde perfiles de estrategia simulados
(sin red): derivadas (calmar/kelly/score), desactivación de losers, pesos,
piso de arb y diagnóstico de factibilidad 1%/2%.
"""
from __future__ import annotations

import pytest

from src.core.orchestration.portfolio import (
    StrategyProfile,
    _compute_derived,
    build_portfolio,
)


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def test_compute_derived_calmar_kelly():
    p = StrategyProfile(name="x", roi=0.08, win_rate=0.55, max_dd_pct=10.0,
                        sharpe=1.2, n_bets=200, avg_odds=2.0)
    _compute_derived(p)
    # calmar = roi / dd = 0.08 / 0.10 = 0.8
    assert p.calmar == pytest.approx(0.8, abs=1e-3)
    # kelly = (w*b - (1-w))/b con b=1 → (0.55-0.45)/1 = 0.10
    assert p.kelly == pytest.approx(0.10, abs=1e-3)
    assert p.score > 0
    assert p.active


def test_persistent_loser_deactivated():
    p = StrategyProfile(name="loser", roi=-0.03, win_rate=0.40, max_dd_pct=20.0,
                        n_bets=120, avg_odds=2.0)
    _compute_derived(p)
    assert p.active is False
    assert p.score == 0.0


def test_small_sample_shrinks_score():
    big = StrategyProfile(name="big", roi=0.06, win_rate=0.54, max_dd_pct=8.0,
                          n_bets=200, avg_odds=2.0)
    small = StrategyProfile(name="small", roi=0.06, win_rate=0.54, max_dd_pct=8.0,
                            n_bets=10, avg_odds=2.0)
    _compute_derived(big)
    _compute_derived(small)
    # misma rentabilidad pero menos muestra → score menor (menos confianza)
    assert small.score < big.score


def test_build_portfolio_weights_sum_to_one():
    arb = StrategyProfile(name="low_risk_arb", bucket="low_risk_arb", roi=0.015,
                          win_rate=0.99, max_dd_pct=1.0, n_bets=300, avg_odds=1.02,
                          source="realized")
    plan = build_portfolio(target_daily_roi=0.01, extra_profiles=[arb])
    assert plan.weights
    assert sum(plan.weights.values()) == pytest.approx(1.0, abs=1e-3)
    # el arb mantiene piso (debe tener peso significativo)
    assert plan.weights.get("low_risk_arb", 0) > 0


def test_feasibility_flags_present():
    arb = StrategyProfile(name="low_risk_arb", bucket="low_risk_arb", roi=0.02,
                          win_rate=0.99, max_dd_pct=1.0, n_bets=300, avg_odds=1.03,
                          source="realized")
    plan = build_portfolio(target_daily_roi=0.01, extra_profiles=[arb])
    assert isinstance(plan.feasible, bool)
    assert isinstance(plan.feasible_2pct, bool)
    assert plan.note
    # 2% siempre debe ser >= exigente que 1% (si factible 2%, factible 1%)
    if plan.feasible_2pct:
        assert plan.feasible


def test_build_portfolio_empty_is_graceful():
    # sin perfiles ni arb → plan vacío pero válido
    plan = build_portfolio(target_daily_roi=0.01)
    assert plan.target_daily_roi == 0.01
    assert plan.expected_daily_roi >= 0.0
    assert plan.note


def test_max_single_weight_cap():
    arb = StrategyProfile(name="low_risk_arb", bucket="low_risk_arb", roi=0.01,
                          win_rate=0.99, max_dd_pct=1.0, n_bets=300, avg_odds=1.02,
                          source="realized")
    # una estrategia dominante no debe acaparar más del cap
    fb = StrategyProfile(name="football_elo_value", bucket="medium_value_stat",
                         roi=0.30, win_rate=0.70, max_dd_pct=5.0, sharpe=3.0,
                         n_bets=500, avg_odds=2.0, source="backtest")
    plan = build_portfolio(target_daily_roi=0.01, extra_profiles=[arb, fb])
    for name, w in plan.weights.items():
        assert w <= 0.96  # tras normalización ningún peso es absurdo


def test_get_active_weights_roundtrip():
    from src.core.orchestration.portfolio import get_active_weights
    arb = StrategyProfile(name="low_risk_arb", bucket="low_risk_arb", roi=0.015,
                          win_rate=0.99, max_dd_pct=1.0, n_bets=300, avg_odds=1.02,
                          source="realized")
    build_portfolio(target_daily_roi=0.01, extra_profiles=[arb])
    w = get_active_weights()
    assert isinstance(w, dict)
    assert "low_risk_arb" in w


def test_allocator_respects_portfolio_deactivation():
    """Si una estrategia está desactivada, el allocator descarta sus value-bets."""
    from src.core.orchestration.portfolio import PORTFOLIO_STATE_KEY
    from src.database.database import AppConfig
    import json
    # football activo (con peso); nfl PROBADA perdedora (active=False en profiles)
    AppConfig.set_value(PORTFOLIO_STATE_KEY, json.dumps({
        "weights": {"low_risk_arb": 0.5, "football_elo_value": 0.5},
        "profiles": [{"name": "nfl_elo_value", "active": False}],
    }))
    from src.core.orchestration.strategy_allocator import StrategyAllocator
    alloc = StrategyAllocator()
    buckets = {"low_risk_arb": [], "medium_value_stat": [
        {"sport": "football", "tipo": "stat", "roi": 5.0, "legs": [{"odd": 2.0}]},
        {"sport": "american_football", "tipo": "stat", "roi": 5.0, "legs": [{"odd": 2.0}]},
    ], "risk_managed_hedge": [], "other": []}
    alloc._apply_portfolio(buckets)
    sports = {o["sport"] for o in buckets["medium_value_stat"]}
    assert "football" in sports          # activa → se mantiene
    assert "american_football" not in sports  # probada perdedora → descartada
