"""Tests de las 2 extensiones del modelo de tenis pedidas explícitamente
por el usuario (2026-07-30): superficie (torneo -> Clay/Hard/Grass, y
p_serve calibrado por superficie con fallback al tour-generico) y
tendencias de secuencia de sets (comeback/cierre). Mockea todo fetch
externo -- nunca pega a Match Charting Project / Flashscore reales."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tournament_surface import (
    extract_tournament_name, get_surface, surface_from_rules_secondary,
)
from outer_apis.sports.tennis.set_sequence_stats import parse_set_scores, get_set_tendencies
from models.volatility.sports.tennis import tennis_markov


def test_extract_tournament_name_from_real_kalshi_rules_secondary():
    rules = (
        "The following market refers to the de Minaur vs Hewitt professional "
        "tennis match in the 2026 ATP Washington Round Of 16 after a ball has "
        "been played. If the match does not occur..."
    )
    assert extract_tournament_name(rules) == "Washington"


def test_extract_tournament_name_none_when_no_match():
    assert extract_tournament_name("some unrelated text") is None
    assert extract_tournament_name(None) is None


def test_get_surface_known_and_unknown():
    assert get_surface("Washington") == "Hard"
    assert get_surface("Roland Garros") == "Clay"
    assert get_surface("Wimbledon") == "Grass"
    assert get_surface("Some New Made Up Tournament") is None
    assert get_surface(None) is None


def test_surface_from_rules_secondary_end_to_end():
    rules = "...match in the 2026 WTA Roland Garros Semifinal after a ball..."
    assert surface_from_rules_secondary(rules) == "Clay"


def test_p_serve_for_rank_falls_back_to_tour_generic_when_surface_table_empty():
    tennis_markov._SURFACE_CALIBRATED_CACHE.clear()
    tennis_markov._CALIBRATED_CACHE.clear()
    with patch("outer_apis.sports.tennis.serve_stats.calibrate_serve_pct_by_bucket_surface", return_value={}):
        with patch("outer_apis.sports.tennis.serve_stats.calibrate_serve_pct_by_bucket", return_value={}):
            p_with_surface = tennis_markov.p_serve_for_rank(5, tour="ATP", surface="Clay")
            p_without_surface = tennis_markov.p_serve_for_rank(5, tour="ATP", surface=None)
    # ambos caen al placeholder estático -- mismo resultado, ninguna excepción.
    assert p_with_surface == p_without_surface


def test_p_serve_for_rank_uses_surface_table_when_bucket_present():
    tennis_markov._SURFACE_CALIBRATED_CACHE.clear()
    tennis_markov._CALIBRATED_CACHE.clear()
    fake_surface_table = {(1, 10): {"p_serve": 0.70, "n_matches": 10, "n_players": 3}}
    with patch("outer_apis.sports.tennis.serve_stats.calibrate_serve_pct_by_bucket_surface",
               return_value=fake_surface_table):
        p = tennis_markov.p_serve_for_rank(5, tour="ATP", surface="Grass")
    assert p == 0.70


def test_parse_set_scores_valid_and_invalid():
    assert parse_set_scores("6-4 3-6 6-2") == [(6, 4), (3, 6), (6, 2)]
    assert parse_set_scores("6-4") is None  # menos de 2 sets
    assert parse_set_scores("6-x 3-6") is None  # token no numérico
    assert parse_set_scores(None) is None
    assert parse_set_scores("") is None


def test_get_set_tendencies_none_when_insufficient_sample():
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=[
        {"score": "6-4 6-3"}, {"score": "6-2 6-1"},
    ]):
        assert get_set_tendencies("Jugadora X") is None  # solo 2 partidos, MIN_MATCHES=5


def test_get_set_tendencies_computes_conditional_rates_with_enough_sample():
    # 5 partidos, todos con set1+set2 resueltos:
    # 3 ganó set1 (de esos, ganó set2 en 2) ; 2 perdió set1 (de esos, remontó ganando set2 en 1)
    matches = [
        {"score": "6-4 6-3"},  # ganó set1, ganó set2
        {"score": "6-4 6-3"},  # ganó set1, ganó set2
        {"score": "6-4 3-6 6-2"},  # ganó set1, perdió set2
        {"score": "3-6 6-4 6-2"},  # perdió set1, remontó set2
        {"score": "3-6 4-6"},  # perdió set1, perdió set2
    ]
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=matches):
        result = get_set_tendencies("Jugadora Y")

    assert result is not None
    assert result["n_matches"] == 5
    assert result["p_win_set1"] == 3 / 5
    # ramas condicionales con solo 3 y 2 observaciones -- por debajo de
    # MIN_MATCHES=5, deben quedar None (fail-closed por sub-muestra).
    assert result["p_win_set2_after_winning_set1"] is None
    assert result["p_win_set2_after_losing_set1"] is None


def test_get_set_tendencies_fills_conditional_rates_with_enough_sub_sample():
    # 10 partidos donde SIEMPRE gana el set 1 (10 obs en esa rama) y de esos
    # gana el set 2 en 6 -- rama "perdió set1" queda vacía (0 obs, None).
    matches = [{"score": "6-4 6-3"}] * 6 + [{"score": "6-4 3-6 6-2"}] * 4
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=matches):
        result = get_set_tendencies("Jugadora Z")

    assert result["n_after_winning_set1"] == 10
    assert result["p_win_set2_after_winning_set1"] == 6 / 10
    assert result["n_after_losing_set1"] == 0
    assert result["p_win_set2_after_losing_set1"] is None
