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
from outer_apis.sports.tennis.set_sequence_stats import (
    get_set_tendencies, get_set_win_rates, parse_set_scores,
)
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


def test_parse_set_scores_valid_and_invalid():
    assert parse_set_scores("6-4, 3-6, 6-2") == [(6, 4), (3, 6), (6, 2)]
    assert parse_set_scores("6-4") is None  # menos de 2 sets
    assert parse_set_scores("6-x, 3-6") is None  # token no numérico
    assert parse_set_scores(None) is None
    assert parse_set_scores("") is None


def test_parse_set_scores_strips_tiebreak_sup_tags():
    """2026-08-17, bug real encontrado antes de construir el filtro de
    resiliencia: el formato REAL de `MatchHistory.score` usa coma como
    separador (no espacio, que era lo que el test/parser viejo
    asumían -- por eso el bug nunca se detectó) y trae el marcador de
    tiebreak `<sup>N</sup>`, en cualquiera de los 2 lados del guión."""
    assert parse_set_scores("7-6<sup>3</sup>, 6-1") == [(7, 6), (6, 1)]
    assert parse_set_scores("6<sup>5</sup>-7, 6-4, 6-2") == [(6, 7), (6, 4), (6, 2)]


def test_get_set_tendencies_none_when_insufficient_sample():
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=[
        {"score": "6-4, 6-3"}, {"score": "6-2, 6-1"},
    ]):
        assert get_set_tendencies("Jugadora X") is None  # solo 2 partidos, MIN_MATCHES=5


def test_get_set_tendencies_computes_conditional_rates_with_enough_sample():
    # 5 partidos, todos con set1+set2 resueltos:
    # 3 ganó set1 (de esos, ganó set2 en 2) ; 2 perdió set1 (de esos, remontó ganando set2 en 1)
    matches = [
        {"score": "6-4, 6-3"},  # ganó set1, ganó set2
        {"score": "6-4, 6-3"},  # ganó set1, ganó set2
        {"score": "6-4, 3-6, 6-2"},  # ganó set1, perdió set2
        {"score": "3-6, 6-4, 6-2"},  # perdió set1, remontó set2
        {"score": "3-6, 4-6"},  # perdió set1, perdió set2
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
    matches = [{"score": "6-4, 6-3"}] * 6 + [{"score": "6-4, 3-6, 6-2"}] * 4
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=matches):
        result = get_set_tendencies("Jugadora Z")

    assert result["n_after_winning_set1"] == 10
    assert result["p_win_set2_after_winning_set1"] == 6 / 10
    assert result["n_after_losing_set1"] == 0
    assert result["p_win_set2_after_losing_set1"] is None


def test_get_set_win_rates_by_set_and_surface():
    matches = [
        {"score": "6-4, 6-3", "surface": "Clay"},
        {"score": "6-4, 3-6, 6-2", "surface": "Clay"},
        {"score": "3-6, 6-4, 6-2", "surface": "Hard"},
        {"score": "6-4, 6-3", "surface": "Hard"},
    ]
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=matches):
        result_clay = get_set_win_rates("Jugadora S", n=10, surface="Clay")
        result_hard = get_set_win_rates("Jugadora S", n=10, surface="Hard")

    assert result_clay is not None
    assert result_clay["p_win_set1"] == 1.0
    assert result_clay["p_win_set2"] == 0.5
    assert result_clay["n_matches"] == 2
    assert result_clay["n_set3"] == 1

    assert result_hard is not None
    assert result_hard["p_win_set1"] == 0.5
    assert result_hard["p_win_set2"] == 1.0
    assert result_hard["p_win_set3"] == 1.0
    assert result_hard["n_matches"] == 2


def test_get_set_tendencies_all_set3_branches():
    matches = [
        {"score": "6-4, 4-6, 6-4"},  # W-L-W
        {"score": "6-4, 4-6, 3-6"},  # W-L-L
        {"score": "4-6, 6-4, 6-4"},  # L-W-W
        {"score": "4-6, 6-4, 4-6"},  # L-W-L
        {"score": "6-4, 6-4, 6-4"},  # W-W-W
        {"score": "4-6, 4-6"},      # L-L no set3
    ]
    with patch("outer_apis.sports.tennis.match_history.get_recent_matches", return_value=matches):
        result = get_set_tendencies("Jugadora T", n=10)

    assert result is not None
    assert result["n_matches"] == 6
    assert result["n_after_winning_set1_winning_set2"] == 1
    assert result["p_win_set3_after_winning_set1_winning_set2"] == 1.0
    assert result["n_after_winning_set1_losing_set2"] == 2
    assert result["p_win_set3_after_winning_set1_losing_set2"] == 0.5
    assert result["n_after_losing_set1_winning_set2"] == 2
    assert result["p_win_set3_after_losing_set1_winning_set2"] == 0.5
    assert result["n_after_losing_set1_losing_set2"] == 0
    assert result["p_win_set3_after_losing_set1_losing_set2"] is None
