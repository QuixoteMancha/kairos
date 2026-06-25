"""
test_settle_bet_dispatcher_and_handicap.py — src.core.sports.settle_bet
(dispatcher) y la rama de liquidacion de handicap en
_settle_cross_book_bets_by_score (settlement.py).

Hallazgo: cross_book_total_arb (ahora real, ver test_cross_book_total_arb.py)
puede tener patas de handicap (side='p1'/'p2', leg_tipo='spread'/etc.) que
ANTES de este fix se hubieran liquidado incorrectamente por el path de
'quien ganó el partido' (side==winner) -- un handicap puede ganar aunque el
equipo pierda, o perder aunque gane, dependiendo de la linea. Se agrego un
dispatcher settle_bet() + una rama dedicada que usa la condicion real
(diff+linea>0) via el settle_bet() de cada deporte.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.sports import settle_bet, ALL_HANDICAP_TIPOS


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


def _make_handicap_bet(side, linea, sport="basketball"):
    leg = MagicMock(side=side, tipo="spread", linea=linea)
    return MagicMock(bet_id="hcp_1", arb_tipo="cross_book_total_arb", bet_type="cross_book_total_arb",
                      fixture_name="Lakers vs Celtics", sport=sport,
                      timestamp="2026-06-20T10:00:00", placed_at=None,
                      legs=[leg], total_staked=2.0, guaranteed_return=4.0, status="pending")


def test_cross_book_handicap_leg_resolves_won_via_settle_bet_not_winner():
    """diff=5 (105-100), linea=-3.5 -> adj=1.5>0 -> WON, aunque el side='p1'
    nunca se compara contra 'quien ganó el partido'."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod

    bet = _make_handicap_bet(side="p1", linea=-3.5)
    resolved = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved["won"] = won
        bet.status = "won" if won else "lost"
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    with patch("src.scrapers.flashscore_scraper.find_team_match_result",
               return_value={"home_score": 105, "away_score": 100}), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_cross_book_bets_by_score(min_hours_past=1.0)

    assert settled == ["hcp_1"]
    assert resolved["won"] is True


def test_cross_book_handicap_leg_resolves_lost_when_margin_insufficient():
    """diff=2 (102-100), linea=-3.5 -> adj=2-3.5=-1.5<0 -> LOST para p1."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod

    bet = _make_handicap_bet(side="p1", linea=-3.5)
    resolved = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved["won"] = won
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    with patch("src.scrapers.flashscore_scraper.find_team_match_result",
               return_value={"home_score": 102, "away_score": 100}), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_cross_book_bets_by_score(min_hours_past=1.0)

    assert settled == ["hcp_1"]
    assert resolved["won"] is False


def test_cross_book_handicap_leg_pushes_to_void_on_exact_line():
    """diff=5, linea=-5.0 -> adj=0 -> PUSH (void, stake devuelto)."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod

    bet = _make_handicap_bet(side="p1", linea=-5.0)
    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=lambda *a, **k: None)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    with patch("src.scrapers.flashscore_scraper.find_team_match_result",
               return_value={"home_score": 105, "away_score": 100}), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"), \
         patch.object(_wmod, "write_ledger") as mock_ledger:
        settled = _s._settle_cross_book_bets_by_score(min_hours_past=1.0)

    assert settled == ["hcp_1"]
    assert bet.status == "void"
    assert bet.pnl == 0.0
    assert fake.balance == 42.0
    assert mock_ledger.called


def test_winner_type_p1_p2_leg_unaffected_by_handicap_branch():
    """Una pata winner real (leg_tipo='ganador', side='p1') NO debe entrar
    por la rama de handicap (leg_tipo no esta en ALL_HANDICAP_TIPOS) --
    sigue resolviendose por 'quien gano el partido', como antes."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod

    leg = MagicMock(side="p1", tipo="ganador", linea=None)
    bet = MagicMock(bet_id="win_1", arb_tipo="cross_book_arb_2way", bet_type="cross_book_arb_2way",
                     fixture_name="Lakers vs Celtics", sport="basketball",
                     timestamp="2026-06-20T10:00:00", placed_at=None,
                     legs=[leg], total_staked=2.0, guaranteed_return=4.0, status="pending")
    resolved = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved["won"] = won
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    with patch("src.scrapers.sofascore_scraper.SofascoreScraper") as MockSofa, \
         patch("src.scrapers.flashscore_scraper.find_team_match_result",
               return_value={"winner": "p1", "resultado": "105-100"}), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        MockSofa.return_value.__enter__.return_value.get_match_score.return_value = None
        settled = _s._settle_cross_book_bets_by_score(min_hours_past=1.0)

    assert settled == ["win_1"]
    assert resolved["won"] is True
