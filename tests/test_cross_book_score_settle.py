"""
test_cross_book_score_settle.py — fallback Sofascore→Flashscore para arbs
cross-book que el live-tracker de Kambi nunca capturó.

Contexto del bug (ver docs/betting_settlement_rules.md, sección "Orden de
resolución para arbs cross-book"): `cross_book_arb_2way`/`_3way` solo se
liquidaban vía Kambi live. Si el torneo nunca apareció en el feed live de
Kambi (ITF, ligas menores), el bet se quedaba pending para siempre: no había
fallback a Sofascore/Flashscore (a diferencia de 'stat', que sí lo tenía vía
`_settle_stat_bets_by_score`), y `_EXPIRABLE_TIPOS` tampoco incluía estos
arb_tipo, así que ni siquiera expiraban a los 3 días.

Estos tests validan `_settle_cross_book_bets_by_score`: debe ignorar bets
recientes, debe preferir Sofascore cuando encuentra el partido, debe caer a
Flashscore cuando Sofascore no lo encuentra (stub actual siempre cae aquí), y
debe dejar el bet pending si ninguna fuente resuelve el partido.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.orchestration import settlement as st


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def _make_arb_legs(combo_key, fixture, legs_spec, sport="tennis", ts="2026-06-10T12:00:00"):
    from src.core.math.simulation import SimBet, BetLeg
    from src.core.orchestration.wallet import get_wallet
    for slug, side, odd, stake in legs_spec:
        w = get_wallet(slug)
        bet = SimBet(
            bet_id=f"{combo_key}_{slug[:3]}_{side}", timestamp=ts,
            fixture_name=fixture,
            arb_tipo="cross_book_arb_3way" if len(legs_spec) == 3 else "cross_book_arb_2way",
            arb_descripcion="t", total_staked=stake, guaranteed_return=round(stake * odd, 2),
            roi=0.03, combo_key=combo_key,
            legs=[BetLeg(market_label=f"{side}@{odd}", side=side, odd=odd, stake=stake,
                          expected_return=round(stake * odd, 2))],
            platform=slug, sport=sport,
        )
        w.bets.append(bet)
        w.balance = round(w.balance - stake, 2)


def test_recent_bet_is_skipped():
    """Un bet colocado hace pocos minutos no debe tocarse (el partido puede seguir en juego)."""
    from datetime import datetime
    from src.core.orchestration.wallet import get_wallet
    now_iso = datetime.now().isoformat()
    _make_arb_legs("cb_recent01", "Player One vs Player Two",
                    [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)], ts=now_iso)
    with patch("src.scrapers.flashscore_scraper.find_match_result") as _fs:
        settled = st._settle_cross_book_bets_by_score(min_hours_past=3.0)
    assert [s for s in settled if s.startswith("cb_recent01")] == []
    _fs.assert_not_called()
    bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_recent01_pin_p1")
    assert bet.status == "pending"


def test_falls_back_to_flashscore_for_tennis_when_sofascore_misses():
    """Sofascore (stub) no encuentra nada → Flashscore decide el ganador."""
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs("cb_itf01", "John Smith vs Carlos Diaz",
                    [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)], sport="tennis")
    with patch("src.scrapers.flashscore_scraper.find_match_result",
               return_value={"resultado": "2-0"}) as _fs:
        settled = st._settle_cross_book_bets_by_score(min_hours_past=0.0)
    _fs.assert_called()
    assert len([s for s in settled if s.startswith("cb_itf01")]) == 2
    pin = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_itf01_pin_p1")
    uni = next(b for b in get_wallet("unibet").bets if b.bet_id == "cb_itf01_uni_p2")
    assert pin.status == "won" and pin.pnl > 0
    assert uni.status == "lost" and uni.pnl < 0


def test_falls_back_to_flashscore_for_team_sport():
    """Liga menor de fútbol no vista por Kambi → Flashscore por equipo resuelve."""
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs("cb_minor01", "Legia Warszawa vs Zielona Gora",
                    [("pinnacle", "draw", 3.2, 4.0), ("unibet", "p2", 4.5, 4.0)], sport="football")
    with patch("src.scrapers.flashscore_scraper.find_team_match_result",
               return_value={"resultado": "1-1", "winner": "draw"}) as _fs:
        settled = st._settle_cross_book_bets_by_score(min_hours_past=0.0)
    _fs.assert_called()
    assert len([s for s in settled if s.startswith("cb_minor01")]) == 2
    draw_bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_minor01_pin_draw")
    p2_bet = next(b for b in get_wallet("unibet").bets if b.bet_id == "cb_minor01_uni_p2")
    assert draw_bet.status == "won"
    assert p2_bet.status == "lost"


def test_no_source_resolves_stays_pending():
    """Ni Sofascore ni Flashscore encuentran el partido → sigue pending (se reintenta en 8h)."""
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs("cb_unknown01", "Obscure United vs Faraway FC",
                    [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)], sport="football")
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=None) as _fs:
        settled = st._settle_cross_book_bets_by_score(min_hours_past=0.0)
    _fs.assert_called()
    assert [s for s in settled if s.startswith("cb_unknown01")] == []
    bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_unknown01_pin_p1")
    assert bet.status == "pending"


def test_sofascore_match_takes_priority_over_flashscore():
    """Si Sofascore SÍ encuentra el partido, no debe consultarse Flashscore."""
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs("cb_sofa01", "Real Strong vs Weak FC",
                    [("pinnacle", "p1", 1.5, 5.0), ("unibet", "p2", 3.0, 5.0)], sport="football")
    with patch("src.scrapers.sofascore_scraper.SofascoreScraper") as _SofaCls, \
         patch("src.scrapers.flashscore_scraper.find_team_match_result") as _fs:
        _inst = _SofaCls.return_value.__enter__.return_value
        _inst.get_match_score.return_value = {"winner": "p1"}
        settled = st._settle_cross_book_bets_by_score(min_hours_past=0.0)
    _fs.assert_not_called()
    assert len([s for s in settled if s.startswith("cb_sofa01")]) == 2
    pin = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_sofa01_pin_p1")
    assert pin.status == "won"


def test_stat_and_kambi_tipos_not_touched():
    """arb_tipo='stat' o no cross-book no debe ser tocado por esta función."""
    from src.core.math.simulation import SimBet, BetLeg
    from src.core.orchestration.wallet import get_wallet
    w = get_wallet("pinnacle")
    bet = SimBet(
        bet_id="stat_untouched_01", timestamp="2026-06-10T12:00:00",
        fixture_name="Some Team vs Other Team", arb_tipo="stat",
        arb_descripcion="t", total_staked=5.0, guaranteed_return=10.0,
        roi=0.03, legs=[BetLeg(market_label="p1@2.0", side="p1", odd=2.0, stake=5.0,
                                expected_return=10.0)],
        platform="pinnacle", sport="football",
    )
    w.bets.append(bet)
    with patch("src.scrapers.flashscore_scraper.find_team_match_result") as _fs:
        st._settle_cross_book_bets_by_score(min_hours_past=0.0)
    _fs.assert_not_called()
    assert next(b for b in get_wallet("pinnacle").bets if b.bet_id == "stat_untouched_01").status == "pending"
