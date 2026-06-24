"""
test_player_endpoints.py — GET /api/player/<id>, /recent-form, /match-history.

Bug fijado: matches_players.py era un stub puro (nunca tuvo implementación
real, confirmado por git history). El stub de /recent-form devolvía la key
"form"; stores/player.js siempre leyó "recent_matches" — ese mismatch
también se corrige aquí.
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
    import src.web.blueprints.matches_players  # noqa: F401  registra las rutas en matches_bp
    from src.database.database import init_db

    init_db()
    app = Flask(__name__)
    app.register_blueprint(matches_bp)
    return app.test_client()


def _make_match(p1, p2, tourn, fecha, period_scores="", ganador=None, surface="hard"):
    from src.database.models.domain import Match

    return Match.create(
        player1=p1, player2=p2, tournament=tourn,
        fecha=fecha, superficie=surface,
        scraper_url=f"https://test/{p1.nombre}-{p2.nombre}-{fecha}-{period_scores}",
        period_scores=period_scores,
        ganador=ganador,
    )


def test_player_detail_not_found(client):
    resp = client.get("/api/player/999999")
    assert resp.status_code == 404


def test_player_detail_real_fields(client):
    from src.database.models.domain import Player

    p = Player.create(nombre="Gaspar Tennisone", ranking=42, ranking_type="ATP", pais="ESP")
    resp = client.get(f"/api/player/{p.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["player"]["nombre"] == "Gaspar Tennisone"
    assert data["player"]["ranking"] == 42
    assert data["stats"] == {}
    assert data["surfaces_available"] == []


def test_recent_form_uses_recent_matches_key(client):
    """Regresión del mismatch de keys: el stub devolvía 'form', el store
    siempre leyó 'recent_matches' — debe ser 'recent_matches', no 'form'."""
    from datetime import date, timedelta
    from src.database.models.domain import Player, Tournament

    p1 = Player.create(nombre="Hidalgo Tennistwo", ranking=10)
    p2 = Player.create(nombre="Ibarra Tennisthree", ranking=20)
    tourn = Tournament.create(nombre="ATP Masters 1000 Test", ubicacion="X", superficie="hard", fecha=date.today())
    _make_match(p1, p2, tourn, date.today() - timedelta(days=1), period_scores="6,4;6,3", ganador=p1)

    resp = client.get(f"/api/player/{p1.id}/recent-form")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "recent_matches" in data
    assert "form" not in data
    m = data["recent_matches"][0]
    assert m["result"] == "W"
    assert m["opponent_name"] == "Ibarra Tennisthree"
    assert m["sets_player"] == 2
    assert m["sets_opponent"] == 0
    assert m["tour_level"] == "1000"


def test_recent_form_loss_and_surface(client):
    from datetime import date, timedelta
    from src.database.models.domain import Player, Tournament

    p1 = Player.create(nombre="Jacaranda Tennisfour", ranking=15)
    p2 = Player.create(nombre="Kowalski Tennisfive", ranking=5)
    tourn = Tournament.create(nombre="ATP Chall Tour Open", ubicacion="X", superficie="clay", fecha=date.today())
    _make_match(p1, p2, tourn, date.today(), period_scores="4,6;3,6", ganador=p2, surface="clay")

    resp = client.get(f"/api/player/{p1.id}/recent-form")
    data = resp.get_json()
    m = data["recent_matches"][0]
    assert m["result"] == "L"
    assert m["sets_player"] == 0
    assert m["sets_opponent"] == 2
    assert m["surface"] == "clay"
    assert m["tour_level"] == "CHALL"


def test_match_history_returns_matches_key(client):
    from src.database.models.domain import Player

    p1 = Player.create(nombre="Lindqvist Tennissix", ranking=33)
    resp = client.get(f"/api/player/{p1.id}/match-history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "matches" in data
    assert data["matches"] == []
