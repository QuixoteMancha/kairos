"""
test_settle_bet_dispatcher_and_handicap.py — src.core.resolvers.sports.settle_bet
(dispatcher) for handicap-style bet types (spread/asian_handicap/etc.).

NOTE (2026-07-10): this file originally also covered the handicap-settlement
branch inside `_settle_cross_book_bets_by_score` (settlement.py). That
function was retired (Sara confirmed zero rows of any status for all legacy
cross-book arb_tipo in the production DB) and its tests removed with it. The
dispatcher tests below (`settle_bet()` / `ALL_HANDICAP_TIPOS`) are unrelated
to cross-book settlement and remain valid.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.resolvers.sports import settle_bet, ALL_HANDICAP_TIPOS


def test_settle_bet_dispatcher_basketball_spread():
    assert settle_bet("basketball", "spread", "p1", -3.5, 105, 100) == "won"
    assert settle_bet("basketball", "spread", "p1", -5.0, 105, 100) == "push"


def test_settle_bet_dispatcher_football_asian_handicap():
    assert settle_bet("football", "asian_handicap", "p1", -1.5, 3, 1) == "won"


def test_settle_bet_dispatcher_tennis_returns_void_by_design():
    """Tenis no tiene settle_bet() de equipo wireado aqui a proposito (ver
    ALL_HANDICAP_TIPOS) -- no inventar un resultado sin fuente verificada."""
    assert settle_bet("tennis", "handicap_juego", "p1", -3.5, 7, 5) == "void"


def test_all_handicap_tipos_excludes_tennis():
    assert "handicap_juego" not in ALL_HANDICAP_TIPOS
    assert "spread" in ALL_HANDICAP_TIPOS
    assert "asian_handicap" in ALL_HANDICAP_TIPOS
