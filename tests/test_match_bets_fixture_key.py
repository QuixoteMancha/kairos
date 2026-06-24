"""
test_match_bets_fixture_key.py — /api/match/<id>/bets debe matchear bets
cuyo fixture_name tiene el orden p1/p2 invertido respecto al match guardado.

Bug fijado: la comparación era un == exacto de "P1 vs P2" contra el
fixture_name del bet. Un bet logueado como "P2 vs P1" (orden invertido,
normal entre distintos feeds/libros) quedaba silenciosamente fuera del
listado de bets del match.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def client():
    from flask import Flask
    from src.web.blueprints.matches_bets import matches_bp
    from src.database.database import init_db

    init_db()
    app = Flask(__name__)
    app.register_blueprint(matches_bp)
    return app.test_client()


def _make_match(p1_name, p2_name, surface="hard"):
    from datetime import date
    from src.database.models.domain import Player, Match, Tournament

    p1, _ = Player.get_or_create(nombre=p1_name)
    p2, _ = Player.get_or_create(nombre=p2_name)
    tourn, _ = Tournament.get_or_create(
        nombre="Test Open", ubicacion="Test City", superficie=surface, fecha=date.today(),
    )
    match = Match.create(
        player1=p1, player2=p2, tournament=tourn,
        fecha=date.today(), superficie=surface,
        scraper_url=f"https://test/{p1_name}-{p2_name}-{date.today()}",
    )
    return match


def _make_bet(bet_id, fixture_name, match_id=None):
    from src.database.models import WalletBet

    WalletBet.create(
        bet_id=bet_id, platform="stake", placed_at="2026-06-10T12:00:00",
        fixture_name=fixture_name, match_id=match_id,
        arb_tipo="cross_book_arb_2way", total_staked=10.0, guaranteed_return=19.0,
        roi=0.03, status="pending",
    )


def test_reversed_fixture_order_still_matches(client):
    match = _make_match("Alice Player", "Bob Player")
    # fixture_name guardado con el orden INVERTIDO (Bob vs Alice) — sin match_id.
    _make_bet("bet_reversed_01", "Bob Player vs Alice Player")

    resp = client.get(f"/api/match/{match.id}/bets")
    assert resp.status_code == 200
    bets = resp.get_json()
    assert any(b.get("id") == "bet_reversed_01" or b.get("bet_id") == "bet_reversed_01" for b in bets)


def test_unrelated_fixture_is_not_matched(client):
    match = _make_match("Carol Player", "Dave Player")
    _make_bet("bet_unrelated_01", "Eve Player vs Frank Player")

    resp = client.get(f"/api/match/{match.id}/bets")
    assert resp.status_code == 200
    bets = resp.get_json()
    assert not any(b.get("id") == "bet_unrelated_01" or b.get("bet_id") == "bet_unrelated_01" for b in bets)
