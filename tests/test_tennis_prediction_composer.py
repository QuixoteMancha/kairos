"""
test_tennis_prediction_composer.py — _elo_p_match / compose_prematch_prediction.

Cobertura dedicada (antes solo indirecta vía test_match_detail_endpoint.py):
- _elo_p_match lee PlayerElo persistido (no reconstruye el engine).
- compose_prematch_prediction elige method="elo" sin PlayerStats reales y
  method="elo+surf" cuando hay datos de saque reales para ambos jugadores.
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
    from src.database.database import init_db
    init_db()
    yield


def _make_player_with_elo(nombre, elo, surface="Hard"):
    from src.database.models.domain import Player, PlayerElo
    p = Player.create(nombre=nombre)
    PlayerElo.create(player=p, surface=surface, elo=elo, matches=20, wins=12)
    PlayerElo.create(player=p, surface="Overall", elo=elo, matches=20, wins=12)
    return p


def test_elo_p_match_favors_higher_rating():
    from src.core.sports.tennis import _elo_p_match

    strong = _make_player_with_elo("Otelo Elohigh", 1700)
    weak = _make_player_with_elo("Petrona Elolow", 1400)

    p = _elo_p_match(strong, weak, "hard")
    assert p > 0.5
    # simetria: invertir el orden debe dar el complemento
    p_rev = _elo_p_match(weak, strong, "hard")
    assert p + p_rev == pytest.approx(1.0, abs=1e-6)


def test_elo_p_match_equal_ratings_is_50_50():
    from src.core.sports.tennis import _elo_p_match

    a = _make_player_with_elo("Quintus Eloeven", 1550)
    b = _make_player_with_elo("Rosalva Eloevena", 1550)
    assert _elo_p_match(a, b, "hard") == pytest.approx(0.5, abs=1e-6)


def test_elo_p_match_unknown_players_default_to_neutral():
    """Jugadores sin fila PlayerElo -> ELO_START para ambos -> 0.5."""
    from src.database.models.domain import Player
    from src.core.sports.tennis import _elo_p_match

    a = Player.create(nombre="Severo Noelorow")
    b = Player.create(nombre="Tabita Noelotwo")
    assert _elo_p_match(a, b, "hard") == pytest.approx(0.5, abs=1e-6)


def test_compose_prematch_prediction_uses_elo_method_without_stats():
    from src.core.sports.tennis import compose_prematch_prediction

    a = _make_player_with_elo("Ulises Composeone", 1650)
    b = _make_player_with_elo("Valeria Composetwo", 1500)

    out = compose_prematch_prediction(a, b, surface="hard")
    assert out["method"] == "elo"
    assert out["bo"] == 3
    assert out["prob_match_p1"] > 0.5
    assert "games_p1_total" in out and "games_p2_total" in out


def test_compose_prematch_prediction_uses_elo_surf_method_with_real_stats():
    from src.database.models.domain import PlayerStats
    from src.core.sports.tennis import compose_prematch_prediction

    a = _make_player_with_elo("Wilfredo Composestat", 1600)
    b = _make_player_with_elo("Ximena Composestattwo", 1550)
    ps1 = PlayerStats.create(
        player=a, superficie="hard",
        porcentaje_primer_servicio=0.62, porcentaje_primer_servicio_ganado=0.74,
        porcentaje_segundo_servicio_ganado=0.52, break_points_convertidos=0.40,
    )
    ps2 = PlayerStats.create(
        player=b, superficie="hard",
        porcentaje_primer_servicio=0.58, porcentaje_primer_servicio_ganado=0.70,
        porcentaje_segundo_servicio_ganado=0.49, break_points_convertidos=0.38,
    )

    out = compose_prematch_prediction(a, b, surface="hard", ps1=ps1, ps2=ps2)
    assert out["method"] == "elo+surf"
    assert 0.0 < out["prob_match_p1"] < 1.0


def test_compose_prematch_prediction_falls_back_to_elo_with_incomplete_stats():
    """Si solo uno de los dos tiene stats reales de saque, sigue method='elo'."""
    from src.database.models.domain import PlayerStats
    from src.core.sports.tennis import compose_prematch_prediction

    a = _make_player_with_elo("Yago Composepartial", 1600)
    b = _make_player_with_elo("Zoraida Composepartialtwo", 1550)
    ps1 = PlayerStats.create(
        player=a, superficie="hard",
        porcentaje_primer_servicio=0.62, porcentaje_primer_servicio_ganado=0.74,
        porcentaje_segundo_servicio_ganado=0.52,
    )
    # ps2 sin datos reales de saque (todo en 0 / default)
    ps2 = PlayerStats.create(player=b, superficie="hard")

    out = compose_prematch_prediction(a, b, surface="hard", ps1=ps1, ps2=ps2)
    assert out["method"] == "elo"
