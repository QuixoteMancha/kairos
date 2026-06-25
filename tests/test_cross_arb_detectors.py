"""
test_cross_arb_detectors.py — src/core/math/arbitrage/detectors/cross.py.

Bug fijado: el modulo entero estaba stubbed (_evaluate_deterministic
retornaba None siempre; los 7 scan_cross_* restantes retornaban [] sin
ninguna logica) pese a ser llamado desde paths productivos reales
(arb_scanner.py -> wallet_bets.py, hedge_routes.py). Cada estrategia se
verifica con un ejemplo numerico calculado a mano, no solo "retorna algo".
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.core.math.arbitrage.opportunity import MarketLine
from src.database.database import BetMarket
from src.core.math.arbitrage.detectors.cross import (
    scan_cross_winner_sets,
    _solve_coverage_stakes,
)


# ── _solve_coverage_stakes: el solver LP generico ──────────────────────────────

def test_solve_coverage_stakes_rejects_uncovered_outcome():
    # outcome 2 no tiene NINGUNA leg ganadora -> debe rechazar sin importar las odds.
    win_matrix = [
        [True, False],
        [False, False],
    ]
    is_arb, stakes, roi = _solve_coverage_stakes(win_matrix, [2.0, 2.0])
    assert is_arb is False


def test_solve_coverage_stakes_symmetric_no_arb():
    # 3 legs, odds=2.0 cada una, cada leg cubre 1 outcome propio + comparte otros
    # (mismo win_matrix que el caso de winner+sets) -> overround=1.5, no arb.
    win_matrix = [
        [True, False, True, True, False, False],
        [False, True, False, True, False, True],
        [False, True, True, False, True, False],
    ]
    is_arb, stakes, roi = _solve_coverage_stakes(win_matrix, [2.0, 2.0, 2.0])
    assert is_arb is False
    assert roi == pytest.approx(-1.0 / 3.0, abs=1e-6)


def test_solve_coverage_stakes_symmetric_real_arb():
    win_matrix = [
        [True, False, True, True, False, False],
        [False, True, False, True, False, True],
        [False, True, True, False, True, False],
    ]
    is_arb, stakes, roi = _solve_coverage_stakes(win_matrix, [4.0, 4.0, 4.0])
    assert is_arb is True
    assert roi == pytest.approx(1.0 / 3.0, abs=1e-6)
    assert all(s == pytest.approx(1.0 / 3.0, abs=1e-6) for s in stakes)


def test_solve_coverage_stakes_asymmetric_matches_inverse_odds():
    win_matrix = [
        [True, False, True, True, False, False],
        [False, True, False, True, False, True],
        [False, True, True, False, True, False],
    ]
    odds = [3.0, 6.0, 6.0]
    is_arb, stakes, roi = _solve_coverage_stakes(win_matrix, odds)
    # overround = 1/3+1/6+1/6 = 2/3 -> t=1/(2/3)=1.5 -> roi=0.5
    assert is_arb is True
    assert roi == pytest.approx(0.5, abs=1e-6)
    # inverse-odds weighting: x_i = (1/odd_i)/overround
    overround = sum(1.0 / o for o in odds)
    expected = [(1.0 / o) / overround for o in odds]
    for got, exp in zip(stakes, expected):
        assert got == pytest.approx(exp, abs=1e-4)


# ── scan_cross_winner_sets: Match Winner + Set1 Winner + Set2 Winner ──────────

def test_winner_sets_detects_real_arb_with_correct_roi():
    """
    mw odd_p2=4.0, set1 odd_p1=4.0, set2 odd_p1=4.0 -> combo (p2,p1,p1) tiene
    overround = 1/4+1/4+1/4 = 0.75 -> ROI esperado = 1/0.75-1 = 0.3333...
    (verificado a mano trazando las 6 secuencias BO3 antes de escribir el codigo).
    """
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.01, odd_p2=4.0, set_num=None, linea=None)
    ms1 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=1)
    ms2 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=2)

    opps = scan_cross_winner_sets([mw, ms1, ms2], min_roi=0.01, outcomes=None, p_set=0.5, legs_scope_fn=None)

    assert len(opps) == 1
    opp = opps[0]
    assert opp.roi == pytest.approx(1.0 / 3.0, abs=1e-4)
    sides = {l['side'] for l in opp.legs}
    assert sides == {'p1', 'p2'}


def test_winner_sets_no_arb_with_normal_consistent_odds():
    """Cuotas normales consistentes (sin mispricing cruzado) -> ningun combo
    de los 4 evaluados debe calificar como arb."""
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.9, odd_p2=1.9, set_num=None, linea=None)
    ms1 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=1.9, odd_p2=1.9, set_num=1)
    ms2 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=1.9, odd_p2=1.9, set_num=2)

    opps = scan_cross_winner_sets([mw, ms1, ms2], min_roi=0.01, outcomes=None, p_set=0.5, legs_scope_fn=None)
    assert opps == []


def test_winner_sets_returns_empty_without_required_markets():
    """Sin los 3 tipos de mercado (winner + set1 + set2) no debe intentar nada."""
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.5, odd_p2=2.5, set_num=None, linea=None)
    opps = scan_cross_winner_sets([mw], min_roi=0.01, outcomes=None, p_set=0.5, legs_scope_fn=None)
    assert opps == []


def test_winner_sets_respects_min_roi_threshold():
    """Un arb real pero por debajo de min_roi no debe reportarse."""
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.01, odd_p2=4.0, set_num=None, linea=None)
    ms1 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=1)
    ms2 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=2)

    # El arb real es ~33.3% -> pedir min_roi=0.50 debe descartarlo.
    opps = scan_cross_winner_sets([mw, ms1, ms2], min_roi=0.50, outcomes=None, p_set=0.5, legs_scope_fn=None)
    assert opps == []
