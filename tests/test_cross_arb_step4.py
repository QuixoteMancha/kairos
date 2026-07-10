"""
Extension de test_cross_arb_detectors.py para la 5ta estrategia ESTADISTICA
(Paso 4 del plan): scan_cross_both_players_over. A diferencia de las 4
estrategias del Paso 3 (que reusan modelos de probabilidad YA existentes
para aces/DF/set-vs-match), esta requiere un modelo nuevo
(_hedge_probs_both_players_over) -- pero es enumeracion EXACTA (no
aproximacion estadistica) sobre _enum_bo3_sequences, ya que los juegos
totales por jugador se derivan directamente del set-score, sin necesidad
de un modelo Poisson como en aces/DF.

Caso positivo calculado a mano ANTES de escribir el test: se hizo un grid
search sobre l1/l2 con la propia funcion _hedge_probs_both_players_over
para encontrar una combinacion con p_fail por debajo del umbral
(HEDGE_MAX_FAIL_PROB=0.01) -- l1=5.5, l2=5.5, p_set=0.5 da p_fail=0.0,
p_both=0.8870625. Con odd_a=odd_b=1.6: impl=1.25,
roi=(1-0+0.8870625)/1.25-1=0.50965 (recomputado a mano fuera de la
funcion, coincide exacto).
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
    scan_cross_both_players_over,
    _hedge_probs_both_players_over,
)
from src.core.resolvers.sports.tennis import SET_SCORE_PROBS_ATP


def test_hedge_probs_both_players_over_boundary_sanity():
    """Lineas imposibles de fallar (l1=l2=-1, cualquier total de juegos es
    >=0 > -1) -> p_fail debe ser exactamente 0 y p_fail+p_both debe ser ~1
    (no hay zona intermedia posible con limites tan bajos)."""
    p_fail, p_both = _hedge_probs_both_players_over(-1, -1, SET_SCORE_PROBS_ATP, p_set=0.5)
    assert p_fail == pytest.approx(0.0, abs=1e-9)
    assert (p_fail + p_both) == pytest.approx(1.0, abs=1e-6)


def test_both_players_over_detects_pair_with_low_pfail():
    """l1=5.5 (P1 over) + l2=5.5 (P2 over) -> p_fail=0.0, p_both=0.8870625
    (verificado por grid-search llamando _hedge_probs_both_players_over
    directamente). odd_p1=odd_p2=1.6: impl=1.25, roi=0.50965."""
    m_p1 = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p1', linea=5.5)
    m_p2 = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p2', linea=5.5)

    opps = scan_cross_both_players_over([m_p1, m_p2], min_roi=0.02, legs_scope_fn=None)
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(0.50965, abs=1e-3)
    assert opps[0].tipo == 'stat'
    assert opps[0].is_structural_hedge is True


def test_both_players_over_rejects_high_pfail():
    """Lineas tipicas (14.5/14.5) -> p_fail=0.5705, muy por encima de
    HEDGE_MAX_FAIL_PROB=0.01 -> debe rechazar."""
    m_p1 = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p1', linea=14.5)
    m_p2 = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p2', linea=14.5)

    opps = scan_cross_both_players_over([m_p1, m_p2], min_roi=0.02, legs_scope_fn=None)
    assert opps == []


def test_both_players_over_ignores_markets_without_player_side():
    """Mercado de total de juegos del MATCH (player_side=None) no debe
    confundirse con un mercado por jugador."""
    m_match = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side=None, linea=5.5)
    m_p2 = MarketLine(tipo=BetMarket.TIPO_TOTAL_JUEGOS, odd_p1=1.6, odd_p2=1.01, player_side='p2', linea=5.5)

    opps = scan_cross_both_players_over([m_match, m_p2], min_roi=0.02, legs_scope_fn=None)
    assert opps == []
