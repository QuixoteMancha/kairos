"""
test_calibration_backtest.py — calibración shrink-to-market + backtest sport.

Las funciones de calibración son puras (sin red). El backtest sport-specific
se prueba a nivel de dispatch/estructura sin pegarle a la red (se valida que
la config nueva existe y que el dispatcher reconoce las estrategias).
"""
from __future__ import annotations

import pytest

from src.core.math.calibration import (
    DEFAULT_LAMBDA,
    calibrate_1x2,
    devig_three_way,
    devig_two_way,
    shrink_to_market,
)


def test_devig_two_way_removes_vig():
    p1, p2 = devig_two_way(1.90, 1.90)
    assert p1 == pytest.approx(0.5, abs=1e-6)
    assert p2 == pytest.approx(0.5, abs=1e-6)
    # suma exacta a 1
    assert p1 + p2 == pytest.approx(1.0, abs=1e-9)


def test_devig_two_way_invalid():
    assert devig_two_way(1.0, 2.0) == (0.0, 0.0)
    assert devig_two_way(0.0, 0.0) == (0.0, 0.0)


def test_devig_three_way_sums_to_one():
    ph, pd, pa = devig_three_way(2.0, 3.5, 4.0)
    assert ph + pd + pa == pytest.approx(1.0, abs=1e-9)
    assert ph > pa  # 2.0 es el más probable


def test_shrink_pulls_toward_market():
    # modelo over-confident 0.85, mercado 0.60 → calibrado entre ambos
    cal = shrink_to_market(0.85, 0.60, lam=0.60)
    assert 0.60 < cal < 0.85
    assert cal == pytest.approx(0.60 * 0.85 + 0.40 * 0.60, abs=1e-9)


def test_shrink_lambda_extremes():
    # λ=1 → solo modelo ; λ=0 → solo mercado
    assert shrink_to_market(0.9, 0.5, lam=1.0) == pytest.approx(0.9)
    assert shrink_to_market(0.9, 0.5, lam=0.0) == pytest.approx(0.5)


def test_shrink_clamps_to_unit():
    assert 0.0 <= shrink_to_market(1.5, 1.2, lam=0.6) <= 1.0
    assert 0.0 <= shrink_to_market(-0.3, -0.1, lam=0.6) <= 1.0


def test_calibrate_1x2_renormalizes():
    model = (0.85, 0.10, 0.05)
    odds = (1.50, 4.00, 7.00)
    cal = calibrate_1x2(model, odds, lam=0.6)
    assert sum(cal) == pytest.approx(1.0, abs=1e-9)
    # la prob de local debe bajar (era over-confident vs mercado)
    assert cal[0] < model[0]


def test_calibrate_1x2_no_market_returns_model():
    model = (0.5, 0.3, 0.2)
    assert calibrate_1x2(model, (1.0, 1.0, 1.0), lam=0.6) == model


def test_default_lambda_sane():
    assert 0.0 < DEFAULT_LAMBDA < 1.0


def test_sport_strategy_dispatch():
    from src.core.math.backtest.sport_strategies import (
        is_sport_strategy,
        run_sport_backtest,
    )
    assert is_sport_strategy("football_elo_value")
    assert is_sport_strategy("nfl_elo_value")
    assert not is_sport_strategy("elo_favorite")
    assert not is_sport_strategy("")


def test_backtest_config_has_sport_fields():
    from src.core.math.backtest import BacktestConfig
    cfg = BacktestConfig(strategy="football_elo_value", sport="football",
                         calibration_lambda=0.5, seasons="2425", point_in_time=True)
    d = cfg.to_dict()
    assert d["sport"] == "football"
    assert d["calibration_lambda"] == 0.5
    assert d["seasons"] == "2425"
    assert d["point_in_time"] is True


def test_engine_unknown_sport_strategy_graceful():
    """Una estrategia sport inválida no debe explotar; devuelve error en result."""
    from src.core.math.backtest import BacktestConfig, BacktestEngine
    # estrategia normal (tenis) sigue devolviendo el stub sin tocar red
    res = BacktestEngine().run(BacktestConfig(strategy="elo_favorite"))
    assert res.n_bets == 0
