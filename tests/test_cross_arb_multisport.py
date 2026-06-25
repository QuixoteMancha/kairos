"""
test_cross_arb_multisport.py — generalizacion multi-deporte de los
cross-arb de gap de linea (Over/Under + Handicap).

Hallazgo de la auditoria: scan_cross_over_under/scan_cross_handicap_lines
estaban etiquetados como "Generic cross ... applicable across sports" en
arb_scanner.py pero hardcodeaban los strings de tipo de tenis
(TIPO_TOTAL_JUEGOS, TIPO_HANDICAP_JUEGO, etc.) -- para cualquier otro
deporte no hacian NADA (cero matches, sin crashear). La matematica del gap
(over gana si val>linea; handicap gana si diff+linea>0) ya es genuinamente
generica -- se verifico idéntica contra settle_bet() real de basketball/
baseball/ice_hockey/american_football. El unico cambio necesario era
resolver el tipo de mercado correcto por deporte
(SportConfig.cross_total_tipos/cross_handicap_tipos).

Football queda excluido a proposito: src/core/sports/football.py no tiene
settle_bet() todavia, asi que no hay logica de liquidacion real contra la
cual verificar que la cobertura es correcta.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.core.math.arbitrage.opportunity import MarketLine
from src.core.math.arbitrage.detectors.cross import scan_cross_over_under, scan_cross_handicap_lines


def test_basketball_total_points_gap_detects_real_arb():
    """Over 210.5 (odd 4.0) + Under 212.5 (odd 4.0) en total_points
    (basketball) -- misma estructura matematica que tenis total_juegos,
    solo cambia el nombre del tipo de mercado. impl=1/4+1/4=0.5, roi=1.0."""
    m_over = MarketLine(tipo='total_points', odd_p1=4.0, odd_p2=1.01, linea=210.5)
    m_under = MarketLine(tipo='total_points', odd_p1=1.01, odd_p2=4.0, linea=212.5)
    opps = scan_cross_over_under([m_over, m_under], min_roi=0.01, legs_scope_fn=None, sport='basketball')
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(1.0, abs=1e-6)


def test_basketball_spread_gap_detects_real_arb_with_correct_direction():
    """spread +2.5 (P1, odd 4.0) + spread -1.5 (P2, odd 4.0). Verificado a
    mano contra basketball.settle_bet: P1 gana si diff>-2.5, P2 gana si
    diff<1.5 -> cubren todo diff fuera de (-2.5,1.5) y ambos ganan dentro.
    impl=1/4+1/4=0.5, roi=1.0."""
    m_p1 = MarketLine(tipo='spread', odd_p1=4.0, odd_p2=1.01, linea=2.5)
    m_p2 = MarketLine(tipo='spread', odd_p1=1.01, odd_p2=4.0, linea=-1.5)
    opps = scan_cross_handicap_lines([m_p1, m_p2], min_roi=0.01, legs_scope_fn=None, sport='basketball')
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(1.0, abs=1e-6)


def test_basketball_spread_gap_rejects_wrong_direction():
    """Mismas lineas pero invertidas (linea_p1 < linea_p2) -- NO hay
    cobertura garantizada, debe rechazar (sanity check de la direccion del
    gap, no solo de que algo se detecte)."""
    m_p1 = MarketLine(tipo='spread', odd_p1=4.0, odd_p2=1.01, linea=-1.5)
    m_p2 = MarketLine(tipo='spread', odd_p1=1.01, odd_p2=4.0, linea=2.5)
    opps = scan_cross_handicap_lines([m_p1, m_p2], min_roi=0.01, legs_scope_fn=None, sport='basketball')
    assert opps == []


def test_baseball_total_runs_and_run_line_use_same_generic_logic():
    """baseball usa run_line/total_runs con la misma convencion
    (settle_bet: adj=diff+linea>0; total>linea) -- debe generalizar igual."""
    m_over = MarketLine(tipo='total_runs', odd_p1=3.0, odd_p2=1.01, linea=7.5)
    m_under = MarketLine(tipo='total_runs', odd_p1=1.01, odd_p2=3.0, linea=8.5)
    opps = scan_cross_over_under([m_over, m_under], min_roi=0.01, legs_scope_fn=None, sport='baseball')
    assert len(opps) == 1
    # impl=1/3+1/3=0.667, roi=1/0.667-1=0.5
    assert opps[0].roi == pytest.approx(0.5, abs=1e-3)


def test_tennis_default_sport_unaffected_by_generalization():
    """sport='tennis' (o sin pasar sport, el default) debe seguir
    comportandose exactamente igual que antes de la generalizacion."""
    m_over = MarketLine(tipo='total_juegos', odd_p1=2.5, odd_p2=1.01, linea=20.5)
    m_under = MarketLine(tipo='total_juegos', odd_p1=1.01, odd_p2=2.5, linea=21.5)
    opps = scan_cross_over_under([m_over, m_under], min_roi=0.01, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.25, abs=1e-6)


def test_football_total_goals_detects_real_arb_now_that_settle_bet_exists():
    """Football ahora tiene settle_bet() (total_goals: total>linea, misma
    convencion que los demas deportes) -- cross_total_tipos ya no esta vacio,
    debe detectar el gap igual que basketball/baseball."""
    m_over = MarketLine(tipo='total_goals', odd_p1=4.0, odd_p2=1.01, linea=2.5)
    m_under = MarketLine(tipo='total_goals', odd_p1=1.01, odd_p2=4.0, linea=3.5)
    opps = scan_cross_over_under([m_over, m_under], min_roi=0.01, legs_scope_fn=None, sport='football')
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(1.0, abs=1e-6)


def test_football_asian_handicap_detects_real_arb_with_correct_direction():
    """asian_handicap +2.5 (P1) + asian_handicap -1.5 (P2). Verificado a mano
    contra football.settle_bet: P1 gana si diff>-2.5, P2 gana si diff<1.5."""
    m_p1 = MarketLine(tipo='asian_handicap', odd_p1=4.0, odd_p2=1.01, linea=2.5)
    m_p2 = MarketLine(tipo='asian_handicap', odd_p1=1.01, odd_p2=4.0, linea=-1.5)
    opps = scan_cross_handicap_lines([m_p1, m_p2], min_roi=0.01, legs_scope_fn=None, sport='football')
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(1.0, abs=1e-6)
