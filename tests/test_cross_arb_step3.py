"""
Extension de test_cross_arb_detectors.py para las 4 estrategias ESTADISTICAS
(Paso 3 del plan): scan_cross_set_under_match_over, scan_cross_set_under_player_over,
scan_cross_ace_df_same_player, scan_cross_ace_cross_player.

Cada caso positivo fue calculado a mano ANTES de escribir el codigo:
impl=1/odd_a+1/odd_b; expected_roi=(1-p_fail+p_both)/impl-1; los p_fail/p_both
se obtuvieron llamando directamente a las funciones _hedge_probs_* ya
existentes (no inventadas para este test).
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
    scan_cross_set_under_match_over,
    scan_cross_set_under_player_over,
    scan_cross_ace_df_same_player,
    scan_cross_ace_cross_player,
)


def test_set_under_match_over_detects_pair_with_low_pfail():
    """l1=12.5 (set under) + l2=18.5 (match over) -> p_fail=0.0, p_both=0.6152
    (calculado llamando _hedge_probs_set_match_bo3 directamente). Con
    odd_under=1.9, odd_over=1.5: impl=1.193, roi=(1.6152/1.193)-1=0.3539."""
    m_set = MarketLine(tipo=BetMarket.TIPO_SET_TOTAL_JUEGOS, odd_p1=1.01, odd_p2=1.9, set_num=1, linea=12.5)
    m_match = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.5, odd_p2=1.01, player_side=None, linea=18.5)

    opps = scan_cross_set_under_match_over([m_set, m_match], min_roi=0.02, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.3539, abs=1e-3)
    assert opps[0].tipo == 'stat'
    assert opps[0].is_structural_hedge is True


def test_set_under_match_over_rejects_high_pfail():
    """l1=8.5 -> p_fail=0.157, muy por encima de HEDGE_MAX_FAIL_PROB=0.01."""
    m_set = MarketLine(tipo=BetMarket.TIPO_SET_TOTAL_JUEGOS, odd_p1=1.01, odd_p2=1.9, set_num=1, linea=8.5)
    m_match = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.5, odd_p2=1.01, player_side=None, linea=20.5)
    opps = scan_cross_set_under_match_over([m_set, m_match], min_roi=0.02, legs_scope_fn=None)
    assert opps == []


def test_set_under_player_over_detects_pair():
    """l1=13.5 (set under) + l2=8.5 (jugador over) -> p_fail=0.0, p_both=0.8313.
    odd_under=1.8, odd_over=1.6: impl=1.1806, roi=(1.8313/1.1806)-1=0.5512."""
    m_set = MarketLine(tipo=BetMarket.TIPO_SET_TOTAL_JUEGOS, odd_p1=1.01, odd_p2=1.8, set_num=1, linea=13.5)
    m_player = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p1', linea=8.5)

    opps = scan_cross_set_under_player_over([m_set, m_player], min_roi=0.02, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.5512, abs=1e-3)


def test_ace_df_same_player_detects_pair():
    """Aces Over 6.5 + DF Under 3.5 (mismo jugador) -> p_fail=0.00017,
    p_both=0.57147. odd_ace=1.7, odd_df=1.7: impl=1.1765, roi=0.3356."""
    m_ace = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.7, odd_p2=1.01, player_side='p1', linea=6.5)
    m_df = MarketLine(tipo=BetMarket.TIPO_DOBLES_FALTAS, odd_p1=1.01, odd_p2=1.7, player_side='p1', linea=3.5)

    opps = scan_cross_ace_df_same_player([m_ace, m_df], min_roi=0.02, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.3356, abs=1e-3)


def test_ace_df_same_player_ignores_different_players():
    """No debe cruzar aces de un jugador con DF de otro."""
    m_ace = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.7, odd_p2=1.01, player_side='p1', linea=6.5)
    m_df = MarketLine(tipo=BetMarket.TIPO_DOBLES_FALTAS, odd_p1=1.01, odd_p2=1.7, player_side='p2', linea=3.5)
    opps = scan_cross_ace_df_same_player([m_ace, m_df], min_roi=0.02, legs_scope_fn=None)
    assert opps == []


def test_ace_cross_player_detects_pair():
    """P1 Aces Over 3.5 + P2 Aces Under 8.5 -> p_fail=0.0, p_both=0.5476.
    odd_a=1.5, odd_b=1.5: impl=1.3333, roi=0.1607."""
    m_a = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.5, odd_p2=1.01, player_side='p1', linea=3.5)
    m_b = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.01, odd_p2=1.5, player_side='p2', linea=8.5)

    opps = scan_cross_ace_cross_player([m_a, m_b], min_roi=0.02, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.1607, abs=1e-3)


def test_ace_cross_player_rejects_weak_correlation_pairs():
    """Lineas tipicas (6.5 vs 6.5) -> correlacion cruzada debil da p_fail alto
    (~0.17), por encima del umbral -> debe rechazar."""
    m_a = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.7, odd_p2=1.01, player_side='p1', linea=6.5)
    m_b = MarketLine(tipo=BetMarket.TIPO_ACES, odd_p1=1.01, odd_p2=1.7, player_side='p2', linea=6.5)
    opps = scan_cross_ace_cross_player([m_a, m_b], min_roi=0.02, legs_scope_fn=None)
    assert opps == []
