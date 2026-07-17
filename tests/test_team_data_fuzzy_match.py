"""Tests para el fuzzy matching de nombres de equipo en team_data/common.py.

Cubre el bug real reportado: nombres de 3 palabras (ciudad+mascota completo,
ej. 'New York Yankees') no matcheaban contra nombres cortos guardados por las
APIs de origen (ej. 'Yankees') porque el score usaba max(len(target), len(toks))
como denominador, penalizando la diferencia de longitud en vez de reconocer
que 'yankees' está totalmente contenido en 'new york yankees'.
"""
import pytest

from src.scrapers.team_data.common import (
    _token_overlap_score,
    get_team_rating,
    get_team_rating_extra,
    upsert_team_rating,
)


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import TeamRating, init_db

    init_db()
    TeamRating.delete().execute()
    yield
    TeamRating.delete().execute()


class TestTokenOverlapScore:
    def test_subset_is_high_confidence(self):
        assert _token_overlap_score({"yankees"}, {"new", "york", "yankees"}) == 1.0
        assert _token_overlap_score({"new", "york", "yankees"}, {"yankees"}) == 1.0

    def test_partial_overlap_uses_ratio(self):
        # 'real madrid' vs 'madrid' -> subset, high confidence
        assert _token_overlap_score({"real", "madrid"}, {"madrid"}) == 1.0

    def test_no_overlap_is_zero(self):
        assert _token_overlap_score({"lakers"}, {"celtics"}) == 0.0

    def test_genuine_partial_overlap_not_subset(self):
        # two-token overlap out of 3+3 tokens, neither subset of other
        score = _token_overlap_score({"real", "madrid", "cf"}, {"atletico", "madrid", "cf"})
        assert 0 < score < 1.0


class TestGetTeamRatingFuzzy:
    def test_full_city_mascot_name_matches_short_stored_name(self):
        # bug real: MLB Stats API guarda "Yankees" (nombre corto)
        upsert_team_rating("mlb", "Yankees", 1550.0, n_games=10)
        assert get_team_rating("mlb", "New York Yankees") == 1550.0

    def test_los_angeles_dodgers(self):
        upsert_team_rating("mlb", "Dodgers", 1600.0, n_games=10)
        assert get_team_rating("mlb", "Los Angeles Dodgers") == 1600.0

    def test_boston_red_sox(self):
        upsert_team_rating("mlb", "Red Sox", 1520.0, n_games=10)
        assert get_team_rating("mlb", "Boston Red Sox") == 1520.0

    def test_existing_case_still_matches_real_madrid(self):
        upsert_team_rating("football", "Madrid", 1900.0, n_games=10)
        assert get_team_rating("football", "Real Madrid") == 1900.0

    def test_existing_case_still_matches_colorado_rockies(self):
        upsert_team_rating("mlb", "Rockies", 1400.0, n_games=10)
        assert get_team_rating("mlb", "Colorado Rockies") == 1400.0

    def test_genuine_no_match_returns_none(self):
        upsert_team_rating("mlb", "Yankees", 1550.0, n_games=10)
        assert get_team_rating("mlb", "Lakers") is None

    def test_no_match_across_sports(self):
        upsert_team_rating("mlb", "Yankees", 1550.0, n_games=10)
        # buscando en 'nba' donde no hay filas -> None
        assert get_team_rating("nba", "New York Yankees") is None

    def test_exact_norm_match_still_works(self):
        upsert_team_rating("mlb", "Yankees", 1550.0, n_games=10)
        assert get_team_rating("mlb", "Yankees") == 1550.0

    def test_unrelated_team_with_shared_noise_word_not_confused(self):
        # 'city' es palabra de ruido y se elimina en normalize_team_name,
        # así que 'Kansas City Royals' normaliza a 'kansas royals' vs
        # 'Kansas City Chiefs' (nfl) -> 'kansas chiefs'; no deberían cruzarse
        # entre sí ni dar falso positivo.
        upsert_team_rating("mlb", "Royals", 1300.0, n_games=10)
        upsert_team_rating("nfl", "Chiefs", 1700.0, n_games=10)
        assert get_team_rating("mlb", "Kansas City Royals") == 1300.0
        assert get_team_rating("nfl", "Kansas City Chiefs") == 1700.0
        # cross-sport lookup for a name not present in that sport -> None
        assert get_team_rating("nfl", "Kansas City Royals") is None


class TestGetTeamRatingExtraFuzzy:
    def test_full_name_matches_short_stored_name(self):
        upsert_team_rating("nhl", "Avalanche", 1500.0, n_games=10, extra={"goals_for": 3.2})
        result = get_team_rating_extra("nhl", "Colorado Avalanche")
        assert result == {"goals_for": 3.2}

    def test_below_min_games_returns_none(self):
        upsert_team_rating("nhl", "Blackhawks", 1400.0, n_games=1, extra={"goals_for": 2.1})
        assert get_team_rating_extra("nhl", "Chicago Blackhawks", min_games=3) is None

    def test_no_match_returns_none(self):
        upsert_team_rating("nhl", "Avalanche", 1500.0, n_games=10, extra={"goals_for": 3.2})
        assert get_team_rating_extra("nhl", "Nonexistent Team") is None
