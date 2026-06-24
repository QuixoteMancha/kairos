"""
test_market_outcome_log.py — _log_market_outcomes (wallet_bets.py).

Bug fijado: era un stub que solo hacía print() — market_outcome_log nunca se
poblaba, así que /api/analytics/strategy y /api/analytics/player/<id>/markets
siempre devolvían vacío. Ahora inserta una fila real por leg resuelto
(won/lost), resolviendo match_id/player_id/superficie/tournament cuando el
fixture matchea un Match real (orden-independiente).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db, MarketOutcomeLog
    init_db()
    MarketOutcomeLog.delete().execute()
    yield
    MarketOutcomeLog.delete().execute()


def _make_bet(bet_id, fixture_name, status, pnl, legs):
    from src.core.math.simulation import SimBet
    return SimBet(
        bet_id=bet_id, timestamp="2026-06-10T12:00:00", fixture_name=fixture_name,
        arb_tipo="stat", arb_descripcion="t", total_staked=sum(l.stake for l in legs),
        guaranteed_return=0.0, roi=0.0, legs=legs, status=status, pnl=pnl,
    )


def test_logs_one_row_per_leg_with_player_resolved():
    from datetime import date
    from src.core.math.simulation import BetLeg
    from src.database.models.domain import Player, Match, Tournament
    from src.database.database import MarketOutcomeLog
    from src.web.blueprints.wallet_bets import _log_market_outcomes

    p1 = Player.create(nombre="Marcelin Logtestone", ranking=7)
    p2 = Player.create(nombre="Nataniel Logtesttwo", ranking=12)
    tourn = Tournament.create(nombre="Log Test Open", ubicacion="X", superficie="clay", fecha=date.today())
    Match.create(
        player1=p1, player2=p2, tournament=tourn, fecha=date.today(), superficie="clay",
        scraper_url="https://test/logtest-match",
    )

    leg = BetLeg(market_label="Aces over 8.5", side="p1", odd=1.9, stake=5.0,
                 expected_return=9.5, player_side="p1", tipo="total_aces", linea=8.5)
    # fixture_name con orden invertido respecto al Match (p2 vs p1) — debe
    # matchear igual (orden-independiente, mismo fix que matches_bets.py).
    bet = _make_bet("bet_logtest_01", "Nataniel Logtesttwo vs Marcelin Logtestone", "won", 4.5, [leg])

    _log_market_outcomes(bet)

    rows = list(MarketOutcomeLog.select().where(MarketOutcomeLog.bet_id == "bet_logtest_01"))
    assert len(rows) == 1
    row = rows[0]
    assert row.won is True
    assert row.tipo == "total_aces"
    assert row.linea == 8.5
    assert row.player_name == "Marcelin Logtestone"
    assert row.player_id == p1.id
    assert row.superficie == "clay"
    assert row.tournament == "Log Test Open"
    assert row.pnl_leg == 4.5
    assert row.odd == 1.9
    assert row.stake == 5.0


def test_pending_and_void_are_not_logged():
    from src.core.math.simulation import BetLeg
    from src.database.database import MarketOutcomeLog
    from src.web.blueprints.wallet_bets import _log_market_outcomes

    leg = BetLeg(market_label="x", side="p1", odd=1.8, stake=5.0, expected_return=9.0)
    for status in ("pending", "void"):
        bet = _make_bet(f"bet_skip_{status}", "Foo vs Bar", status, 0.0, [leg])
        _log_market_outcomes(bet)

    count = MarketOutcomeLog.select().where(
        MarketOutcomeLog.bet_id.in_(["bet_skip_pending", "bet_skip_void"])
    ).count()
    assert count == 0


def test_unknown_fixture_logs_without_player_or_match():
    from src.core.math.simulation import BetLeg
    from src.database.database import MarketOutcomeLog
    from src.web.blueprints.wallet_bets import _log_market_outcomes

    leg = BetLeg(market_label="Moneyline", side="p1", odd=2.0, stake=10.0, expected_return=20.0, tipo="ganador")
    bet = _make_bet("bet_nomatch_01", "Unknown Player A vs Unknown Player B", "lost", -10.0, [leg])
    _log_market_outcomes(bet)

    row = MarketOutcomeLog.get(MarketOutcomeLog.bet_id == "bet_nomatch_01")
    assert row.won is False
    assert row.match_id is None
    assert row.player_id is None
    assert row.pnl_leg == -10.0


def test_pnl_distributed_proportionally_across_multiple_legs():
    from src.core.math.simulation import BetLeg
    from src.database.database import MarketOutcomeLog
    from src.web.blueprints.wallet_bets import _log_market_outcomes

    leg1 = BetLeg(market_label="A", side="p1", odd=1.9, stake=6.0, expected_return=11.4, tipo="ganador")
    leg2 = BetLeg(market_label="B", side="p2", odd=2.1, stake=4.0, expected_return=8.4, tipo="ganador")
    bet = _make_bet("bet_multi_01", "Foo vs Bar", "won", 10.0, [leg1, leg2])
    _log_market_outcomes(bet)

    rows = {r.stake: r.pnl_leg for r in
            MarketOutcomeLog.select().where(MarketOutcomeLog.bet_id == "bet_multi_01")}
    assert len(rows) == 2
    assert rows[6.0] == pytest.approx(6.0)   # 10 * 6/10
    assert rows[4.0] == pytest.approx(4.0)   # 10 * 4/10
