"""
test_tennis_predictions.py — predict_tennis_match / _pset_from_pmatch_bo3.

Estas funciones alimentan el nuevo GET /api/match/<id> (predictions +
games_breakdown). Verifican que la simulación punto-a-punto da resultados
simétricos/coherentes, no números arbitrarios.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.core.resolvers.sports.tennis import (
    predict_tennis_match,
    _pset_from_pmatch_bo3,
    _p_set_from_pgame,
)


def test_symmetric_holds_give_50_50():
    out = predict_tennis_match(0.65, 0.65)
    assert out["prob_match_p1"] == pytest.approx(0.5, abs=1e-6)
    assert out["prob_set_p1"] == pytest.approx(0.5, abs=1e-6)
    assert out["handicap_match"] == pytest.approx(0.0, abs=1e-6)
    assert out["handicap_set"] == pytest.approx(0.0, abs=1e-6)
    assert out["games_p1_total"] == pytest.approx(out["games_p2_total"], abs=1e-6)


def test_clear_favorite_wins_more_and_drops_fewer_games():
    out = predict_tennis_match(0.80, 0.55)
    assert out["prob_match_p1"] > 0.5
    assert out["prob_set_p1"] > 0.5
    # El favorito (mejor saque) debe perder MENOS juegos propios que el rival
    # gana de los suyos en el set representativo... el handicap_match (gA-gB)
    # debe favorecer a A.
    assert out["handicap_match"] > 0
    assert out["match_winner_player"] == 1
    assert out["set_winner_player"] == 1


def test_prob_match_p1_plus_p2_is_one():
    out = predict_tennis_match(0.70, 0.60)
    assert out["prob_match_p1"] + out["prob_match_p2"] == pytest.approx(1.0, abs=1e-9)
    assert out["prob_set_p1"] + out["prob_set_p2"] == pytest.approx(1.0, abs=1e-9)


def test_total_games_match_is_sum_of_per_player_totals():
    out = predict_tennis_match(0.72, 0.58)
    # tolerancia 0.02: cada componente se redondea independientemente a 2
    # decimales antes de sumar, así que puede haber +/-0.01 de cada lado.
    assert out["total_games_match"] == pytest.approx(
        out["games_p1_total"] + out["games_p2_total"], abs=0.02
    )


def test_pset_from_pmatch_roundtrips_with_forward_formula():
    for p_match in (0.5, 0.6, 0.75, 0.9):
        p_set = _pset_from_pmatch_bo3(p_match)
        p_match_back = p_set * p_set * (3.0 - 2.0 * p_set)
        assert p_match_back == pytest.approx(p_match, abs=1e-4)


def test_pset_from_pmatch_symmetric_for_underdog():
    p_set_fav = _pset_from_pmatch_bo3(0.7)
    p_set_dog = _pset_from_pmatch_bo3(0.3)
    assert p_set_fav == pytest.approx(1.0 - p_set_dog, abs=1e-6)
