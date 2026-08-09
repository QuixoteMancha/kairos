"""Tests de `save_next_match` -- 2026-08-09, pedido explícito del
usuario: "puede que no aparezca [el nombre completo] porque el match
aún no habría corrido cuando aparezca en Kalshi y el worker lo
detecte... TennisExplorer en la página de player tiene un campo que
dice next match, ese match tiene que ser validado". Resuelve+persiste
el partido PROGRAMADO (aún no jugado) de la sección "Next match" del
perfil del jugador."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, save_next_match


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()


_NEXT_MATCH = {
    "match_id": "3290769",
    "tournament": "Toronto WTA",
    "tournament_href": "/toronto-wta/2026/wta-women/",
    "round": "quarterfinal",
    "start_raw": "11.08. 02:30",
    "match_text": "Alexandrova E. - Svitolina E.",
    "tour": "WTA",
}

_DETAIL = {
    "match_id": "3290769",
    "match_date": "2026-08-11",
    "tournament": "Toronto WTA",
    "tour": "WTA",
    "round": "quarterfinal",
    "surface": "hard",
    "score": None,
    "winner_side": None,
    "player_a_name": "Alexandrova Ekaterina",
    "player_a_slug": "alexandrova-3181a",
    "player_a_ranking": 19,
    "player_a_birthdate": "1994-11-15",
    "player_a_height_cm": 180,
    "player_a_weight_kg": 66,
    "player_a_plays": "right",
    "player_b_name": "Svitolina Elina",
    "player_b_slug": "svitolina",
    "player_b_ranking": 9,
    "player_b_birthdate": "1994-09-12",
    "player_b_height_cm": 174,
    "player_b_weight_kg": 60,
    "player_b_plays": "right",
}


def test_save_next_match_resolves_full_identity_before_match_is_played():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_next_match", return_value=_NEXT_MATCH,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail", return_value=_DETAIL,
    ):
        result = save_next_match("Elina Svitolina")

    assert result is True
    row = MatchHistory.get(MatchHistory.match_id == "3290769")
    assert row.player_a_name == "Alexandrova Ekaterina"
    assert row.player_b_name == "Svitolina Elina"
    assert row.tour == "WTA"
    assert row.score is None  # todavía no se jugó
    assert row.winner_side is None


def test_save_next_match_false_when_no_next_match_scheduled():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_next_match", return_value=None,
    ):
        assert save_next_match("Elina Svitolina") is False


def test_save_next_match_validates_against_expected_opponent_hint():
    # "ese match tiene que ser validado" -- si el caller ya conoce al
    # rival esperado (ej. por el mercado de Kalshi) y NO coincide con lo
    # que trae "Next match", no se debe confiar en ese match_id.
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_next_match", return_value=_NEXT_MATCH,
    ):
        result = save_next_match("Elina Svitolina", expected_opponent_hint="Someone Unrelated")
    assert result is False
    assert MatchHistory.select().count() == 0


def test_save_next_match_passes_validation_when_hint_matches():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_next_match", return_value=_NEXT_MATCH,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail", return_value=_DETAIL,
    ):
        result = save_next_match("Elina Svitolina", expected_opponent_hint="Alexandrova")
    assert result is True


def test_save_next_match_false_when_identity_does_not_confirm():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_next_match", return_value=_NEXT_MATCH,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value={**_DETAIL, "player_a_name": "Someone Else", "player_b_name": "Another Person"},
    ):
        result = save_next_match("Elina Svitolina")
    assert result is False
