"""
test_match_detail_endpoint.py — GET /api/match/<id>.

Bug fijado: este endpoint no existía (frontend 404 siempre). Verifica que
devuelve match real + predictions/volatility reales (vía ELO persistido +
modelo punto-a-punto), y que p1_stats/volatility son null (no 0.0 fabricado)
cuando no hay datos reales.
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
        nombre="Test Open", ubicacion="Test City, Spain", superficie=surface, fecha=date.today(),
    )
    match = Match.create(
        player1=p1, player2=p2, tournament=tourn,
        fecha=date.today(), superficie=surface,
        scraper_url=f"https://test/{p1_name}-{p2_name}-{date.today()}",
    )
    return match, p1, p2


def test_match_not_found_returns_404(client):
    resp = client.get("/api/match/999999")
    assert resp.status_code == 404


def test_match_detail_shape_with_no_stats_data(client):
    """Sin PlayerElo/PlayerStats/MatchVolatility rows: predictions/volatility
    pueden ser null o reflejar el 0.5 default de ELO, pero p1_stats/p2_stats
    y volatility deben ser null — nunca un 0.0 fabricado."""
    match, p1, p2 = _make_match("Aldana Nowplay", "Bilson Nowplay")

    resp = client.get(f"/api/match/{match.id}")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["match"]["p1"] == "Aldana Nowplay"
    assert data["match"]["p2"] == "Bilson Nowplay"
    assert data["p1_stats"] is None
    assert data["p2_stats"] is None
    assert data["volatility_p1"] is None
    assert data["volatility_p2"] is None
    # Sin historial ELO -> 0.5/0.5 (prior neutral), no None (predictions SÍ
    # se puede calcular siempre vía el fallback p_hold_default).
    assert data["predictions"] is not None
    assert data["predictions"]["prob_match_p1"] == pytest.approx(0.5, abs=0.05)
    assert data["predictions"]["method"] == "elo"
    assert data["games_breakdown"] is not None


def test_match_detail_with_real_playerstats_summary(client):
    from src.database.models.domain import PlayerStats

    match, p1, p2 = _make_match("Cesara Statzone", "Dorina Statzwei")
    PlayerStats.create(player=p1, superficie="all", match_win_rate=0.65, spw=0.68, rpw=0.40, bpc=0.42)

    resp = client.get(f"/api/match/{match.id}")
    data = resp.get_json()
    assert data["p1_stats"]["match_win_rate"] == pytest.approx(0.65)
    assert data["p1_stats"]["spw"] == pytest.approx(0.68)
    # Las rich fields siguen null si no hay datos Sofascore.
    assert data["p1_stats"]["aces_por_partido"] is None
    assert data["p2_stats"] is None


def test_superficie_consultada_echoes_normalized_value(client):
    match, p1, p2 = _make_match("Eliska Surfalpha", "Fenix Surfbeta")
    resp = client.get(f"/api/match/{match.id}", query_string={"superficie": "Clay"})
    data = resp.get_json()
    assert data["superficie_consultada"] == "clay"

    resp2 = client.get(f"/api/match/{match.id}", query_string={"superficie": "garbage"})
    data2 = resp2.get_json()
    assert data2["superficie_consultada"] == "all"
