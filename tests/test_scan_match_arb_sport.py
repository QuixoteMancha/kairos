"""
test_scan_match_arb_sport.py — bug multi-deporte: ArbScanner.sport defaulteaba
a 'football' y scan_match_arb (el unico entry point publico, usado solo por
callers tennis: wallet_bets.py, hedge_routes.py, simulation_engine.py) no
tenia parametro sport -- las 5 estrategias cross-arb especificas de tenis
(if self.sport == "tennis": ...) nunca se ejecutaban en produccion.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.math.arbitrage.arb_scanner import scan_match_arb
from src.core.math.arbitrage.opportunity import MarketLine
from src.database.database import BetMarket


def test_scan_match_arb_defaults_to_tennis_sport():
    """Sin pasar sport explicito (como hacen los 3 callers reales hoy),
    debe seguir activando las estrategias cross-arb de tenis."""
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.01, odd_p2=4.0, set_num=None, linea=None)
    ms1 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=1)
    ms2 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=2)

    opps = scan_match_arb([mw, ms1, ms2], min_roi=0.01, verbose=False)
    assert any(o.tipo == 'cross-arb' for o in opps)


def test_scan_match_arb_explicit_non_tennis_sport_skips_tennis_strategies():
    """Pasar sport='football' explicitamente debe seguir saltando las
    estrategias cross-arb especificas de tenis (comportamiento ya existente,
    solo se confirma que el nuevo parametro respeta el override)."""
    mw = MarketLine(tipo=BetMarket.TIPO_GANADOR, odd_p1=1.01, odd_p2=4.0, set_num=None, linea=None)
    ms1 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=1)
    ms2 = MarketLine(tipo=BetMarket.TIPO_SET_GANADOR, odd_p1=4.0, odd_p2=1.01, set_num=2)

    opps = scan_match_arb([mw, ms1, ms2], min_roi=0.01, verbose=False, sport='football')
    assert not any(o.tipo == 'cross-arb' for o in opps)
