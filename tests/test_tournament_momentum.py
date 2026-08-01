"""Tests del avance de rondas en el torneo actual como señal de forma
(2026-08-01, pedido explícito del usuario: "el ranking puede ser
engañoso, el avance de rondas demuestra que el nivel va mejorando")."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from outer_apis.sports.tennis.tournament_momentum import (
    get_current_tournament_round_wins, get_momentum_implied_win_prob, MAX_ROUNDS_FOR_NORMALIZATION,
)


def test_get_current_tournament_round_wins_none_without_tournament():
    assert get_current_tournament_round_wins("Diana Shnaider", None) is None
    assert get_current_tournament_round_wins("Diana Shnaider", "") is None


def test_get_current_tournament_round_wins_counts_consecutive_wins_same_tournament():
    """Caso real que motivó esto: Shnaider ganó R16 y QF en Washington
    seguidos antes de la semifinal contra Pegula -- 2 rondas ganadas."""
    fake_rows = [
        {"tournament": "Washington", "subject_won": True},   # QF, más reciente
        {"tournament": "Washington", "subject_won": True},   # R16
        {"tournament": "Washington", "subject_won": True},   # R32
        {"tournament": "Berlin", "subject_won": False},       # otro torneo -- corta la racha
    ]
    with patch("outer_apis.sports.tennis.tournament_momentum.fetch_and_cache", return_value=fake_rows):
        wins = get_current_tournament_round_wins("Diana Shnaider", "Washington")
    assert wins == 3


def test_get_current_tournament_round_wins_stops_at_first_loss():
    fake_rows = [
        {"tournament": "Washington", "subject_won": True},
        {"tournament": "Washington", "subject_won": False},  # perdió acá -- la racha activa es 1
        {"tournament": "Washington", "subject_won": True},
    ]
    with patch("outer_apis.sports.tennis.tournament_momentum.fetch_and_cache", return_value=fake_rows):
        wins = get_current_tournament_round_wins("Player X", "Washington")
    assert wins == 1


def test_get_current_tournament_round_wins_zero_when_lost_first_match_this_tournament():
    fake_rows = [{"tournament": "Washington", "subject_won": False}]
    with patch("outer_apis.sports.tennis.tournament_momentum.fetch_and_cache", return_value=fake_rows):
        wins = get_current_tournament_round_wins("Player X", "Washington")
    assert wins == 0


def test_get_current_tournament_round_wins_none_without_data():
    with patch("outer_apis.sports.tennis.tournament_momentum.fetch_and_cache", return_value=[]):
        assert get_current_tournament_round_wins("Nobody", "Washington") is None


def test_get_momentum_implied_win_prob_symmetric_and_bounded():
    assert get_momentum_implied_win_prob(3, 3) == pytest.approx(0.5)
    assert get_momentum_implied_win_prob(None, 2) is None
    assert get_momentum_implied_win_prob(6, 0) == pytest.approx(1.0)
    assert get_momentum_implied_win_prob(0, 6) == pytest.approx(0.0)


def test_get_momentum_implied_win_prob_caps_at_max_rounds():
    # 10 rondas se trata igual que el tope (6) -- no le da más ventaja
    # por una racha absurdamente larga (protección estructural).
    assert get_momentum_implied_win_prob(10, MAX_ROUNDS_FOR_NORMALIZATION) == pytest.approx(0.5)
