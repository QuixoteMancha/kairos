"""
test_inside_arb_3way.py — fix multi-deporte: MarketLine.odd_draw + scan_inside_arb 3-way.

Bug encontrado en auditoria multi-sport: MarketLine.overround solo sumaba
impl_p1+impl_p2 incluso para mercados 1X2 de 3 resultados (futbol/hockey).
Al ignorar la probabilidad implicita del empate, CUALQUIER 1X2 real con
margen normal de book parecia un arb falso-positivo enorme (overround de
solo 2 de 3 resultados mutuamente excluyentes siempre suma <1). Ademas,
scanning.py::_scan_inside_intra_per_book construia el MarketLine sin pasar
odd_draw, descartandolo activamente para mercados tipo='1x2' reales.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.core.math.arbitrage.opportunity import MarketLine
from src.core.math.arbitrage.detectors.inside import scan_inside_arb


def test_market_line_overround_includes_draw_when_present():
    m = MarketLine(tipo='1x2', odd_p1=2.5, odd_p2=3.3, odd_draw=3.4)
    assert m.overround == pytest.approx(1/2.5 + 1/3.3 + 1/3.4, abs=1e-9)


def test_market_line_overround_2way_unaffected_without_draw():
    """Mercados 2-way (tenis ganador, etc.) sin odd_draw deben seguir
    comportandose exactamente igual que antes del fix."""
    m = MarketLine(tipo='ganador', odd_p1=1.9, odd_p2=1.9)
    assert m.overround == pytest.approx(1/1.9 + 1/1.9, abs=1e-9)
    assert m.impl_draw == 0.0


def test_inside_arb_1x2_normal_book_is_not_a_false_positive_arb():
    """Cuotas normales de un 1X2 real (margen de book ~3-5%) -- SIN contar
    el empate, esto falsamente parecia un 'arb' del 42% (overround
    p1+p2=0.703). Contando el empate correctamente, overround real=0.997,
    roi=0.00286 (genuino pero minimo, no un 42% fantasma)."""
    m = MarketLine(tipo='1x2', odd_p1=2.5, odd_p2=3.3, odd_draw=3.4)
    opps = scan_inside_arb([m], min_roi=0.10, legs_scope_fn=lambda x: 'match')
    # Con el bug viejo (sin draw) el overround=0.703 hubiera disparado esto
    # ampliamente incluso con un umbral de 10%; con el fix, el roi real es
    # ~0.29% y no pasa min_roi=0.10.
    assert opps == []


def test_inside_arb_1x2_genuine_mispricing_detects_real_3leg_arb():
    """Libro genuinamente mal calibrado (odds altas en las 3 patas) ->
    arb real de 3 patas. roi=(1-overround)/overround calculado a mano."""
    m = MarketLine(tipo='1x2', odd_p1=2.6, odd_p2=3.5, odd_draw=3.6)
    overround = 1/2.6 + 1/3.5 + 1/3.6
    roi_manual = (1 - overround) / overround

    opps = scan_inside_arb([m], min_roi=0.01, legs_scope_fn=lambda x: 'match')
    assert len(opps) == 1
    assert opps[0].roi == pytest.approx(roi_manual, abs=1e-9)
    sides = {l['side'] for l in opps[0].legs}
    assert sides == {'p1', 'p2', 'draw'}
    # stake_pct de cada pata = impl_i/overround (inverse-odds, igual que el caso 2-way)
    for leg in opps[0].legs:
        impl = 1.0 / leg['odd']
        assert leg['stake_pct'] == pytest.approx(impl / overround, abs=1e-9)


def test_scanning_per_book_intra_passes_through_draw_odd():
    """scanning.py::_scan_inside_intra_per_book ya no debe descartar
    odd_draw al construir el MarketLine para mercados tipo='1x2'."""
    from src.core.orchestration.scanning import _scan_inside_intra_per_book

    class _FakeMarket:
        tipo = '1x2'
        odd_p1 = 2.6
        odd_p2 = 3.5
        odd_draw = 3.6

    class _FakeFixture:
        markets = [_FakeMarket()]

    opps = _scan_inside_intra_per_book({'somebook': [_FakeFixture()]})
    assert len(opps) == 1
    sides = {l['side'] for l in opps[0]['legs']} if isinstance(opps[0], dict) else None
    # to_dict() flattens legs to dicts with 'side' key (see opportunity.py to_dict)
    assert sides == {'p1', 'p2', 'draw'}
