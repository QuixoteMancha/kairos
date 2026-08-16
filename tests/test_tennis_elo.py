"""Tests del motor de Elo puro -- `models/volatility/sports/tennis/tennis_elo.py`.
2026-08-11, pedido explícito del usuario ("sigamos con el Elo propio en
base al historial de matches")."""

from __future__ import annotations

from models.volatility.sports.tennis.tennis_elo import (
    expected_score, update_elo, DEFAULT_INITIAL_ELO, DEFAULT_K_FACTOR,
)


def test_expected_score_equal_ratings_is_50_50():
    assert expected_score(1500, 1500) == 0.5


def test_expected_score_favors_higher_rated_player():
    assert expected_score(1800, 1400) > 0.5
    assert expected_score(1400, 1800) < 0.5


def test_expected_score_symmetric():
    a = expected_score(1600, 1400)
    b = expected_score(1400, 1600)
    assert round(a + b, 6) == 1.0


def test_update_elo_winner_gains_loser_loses():
    new_a, new_b = update_elo(1500, 1500, a_won=True)
    assert new_a > 1500
    assert new_b < 1500


def test_update_elo_is_zero_sum():
    new_a, new_b = update_elo(1500, 1600, a_won=True)
    delta_a = new_a - 1500
    delta_b = new_b - 1600
    assert round(delta_a + delta_b, 6) == 0.0


def test_update_elo_underdog_win_moves_more_than_favorite_win():
    # ganar siendo underdog debe sumar MÁS puntos que ganar siendo
    # favorito, partiendo del mismo gap (400 puntos).
    favorite_win_a, _ = update_elo(1800, 1400, a_won=True)  # A (favorito) gana
    favorite_gain = favorite_win_a - 1800
    underdog_win_a, _ = update_elo(1400, 1800, a_won=True)  # A (underdog) gana
    underdog_gain = underdog_win_a - 1400
    assert underdog_gain > favorite_gain


def test_custom_k_factor_scales_the_change():
    _, loser_elo_small_k = update_elo(1500, 1500, a_won=True, k=8)
    _, loser_elo_big_k = update_elo(1500, 1500, a_won=True, k=64)
    # con K más grande, el perdedor (elo_b) cae más -- queda con un Elo menor.
    assert loser_elo_big_k < loser_elo_small_k


def test_defaults_are_standard_elo_values():
    assert DEFAULT_INITIAL_ELO == 1500.0
    assert DEFAULT_K_FACTOR == 32.0
