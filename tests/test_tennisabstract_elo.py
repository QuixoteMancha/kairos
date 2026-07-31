"""Tests del scraper de Tennis Abstract (Elo real por jugador + yElo de
temporada) y del facade con DB-first + matching de nombres + fórmula
logística de Elo. Mockea toda request HTTP real -- nunca pega a
tennisabstract.com real en tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisabstractelo import TennisAbstractElo, TennisAbstractYelo
from outer_apis.scrapers.tennis.tennisabstract_scraper import _parse_float, _parse_int
from outer_apis.sports.tennis.tennisabstract_elo import get_elo, get_season_form, elo_implied_win_prob


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisAbstractElo.delete().execute()
        TennisAbstractYelo.delete().execute()
    yield
    with db_historical.connection_context():
        TennisAbstractElo.delete().execute()
        TennisAbstractYelo.delete().execute()


def test_parse_float_and_int_fail_closed():
    assert _parse_float("2331.9") == 2331.9
    assert _parse_float("") is None
    assert _parse_float("n/a") is None
    assert _parse_int("1") == 1
    assert _parse_int("") is None


def test_elo_implied_win_prob_symmetric_and_bounded():
    # Elo iguales -> 50/50.
    assert elo_implied_win_prob(2000.0, 2000.0) == pytest.approx(0.5)
    # A mucho más fuerte -> probabilidad alta pero < 1.
    p = elo_implied_win_prob(2400.0, 2000.0)
    assert 0.9 < p < 1.0
    # Simetría: P(A gana) + P(B gana) == 1.
    p_b = elo_implied_win_prob(2000.0, 2400.0)
    assert p + p_b == pytest.approx(1.0)


def test_get_elo_uses_surface_specific_when_available():
    fake_table = {"Jannik Sinner": {"player": "Jannik Sinner", "elo": 2331.9, "h_elo": 2269.3, "c_elo": 2221.8, "g_elo": 2135.5, "atp_rank": 1}}
    with patch("outer_apis.sports.tennis.tennisabstract_elo._fetch_elo_table", return_value=fake_table):
        assert get_elo("Jannik Sinner", "ATP", surface="Clay") == 2221.8
        assert get_elo("Jannik Sinner", "ATP", surface="Grass") == 2135.5
        assert get_elo("Jannik Sinner", "ATP", surface=None) == 2331.9  # sin superficie -> Elo general


def test_get_elo_fuzzy_matches_accented_name():
    fake_table = {"Iga Swiatek": {"player": "Iga Swiatek", "elo": 2058.0, "h_elo": 2018.8, "c_elo": 2005.1, "g_elo": 1903.5, "atp_rank": 8}}
    with patch("outer_apis.sports.tennis.tennisabstract_elo._fetch_elo_table", return_value=fake_table):
        # nombre con tilde (como podría venir de otra fuente) debe matchear igual
        assert get_elo("Iga Świątek", "WTA", surface="Hard") == 2018.8


def test_get_elo_none_when_player_not_found():
    with patch("outer_apis.sports.tennis.tennisabstract_elo._fetch_elo_table", return_value={}):
        assert get_elo("Nobody Ever Ranked", "ATP") is None


def test_get_elo_invalid_tour_returns_none():
    assert get_elo("Jannik Sinner", "NOT_A_TOUR") is None


def test_get_season_form_returns_yelo_and_record():
    fake_table = {"Marta Kostyuk": {"player": "Marta Kostyuk", "yelo": 2152.5, "wins": 28, "losses": 6}}
    with patch("outer_apis.sports.tennis.tennisabstract_elo._fetch_yelo_table", return_value=fake_table):
        result = get_season_form("Marta Kostyuk", "WTA")
    assert result == {"player": "Marta Kostyuk", "yelo": 2152.5, "wins": 28, "losses": 6}


def test_fetch_elo_table_skips_live_scrape_when_db_fresh():
    """Verificado por conteo de llamadas al scraper, no solo el resultado
    -- mismo patrón fail-closed/DB-first que el resto del proyecto."""
    from outer_apis.sports.tennis import tennisabstract_elo
    from database.historical.tennis.writer import save_tennisabstract_elo_snapshot

    save_tennisabstract_elo_snapshot("ATP", [
        {"player": "Jannik Sinner", "elo": 2331.9, "h_elo": 2269.3, "c_elo": 2221.8, "g_elo": 2135.5, "atp_rank": 1},
    ])

    with patch("outer_apis.sports.tennis.tennisabstract_elo.tennisabstract_scraper.get_elo_ratings") as mock_fetch:
        table = tennisabstract_elo._fetch_elo_table("ATP")

    mock_fetch.assert_not_called()
    assert table["Jannik Sinner"]["elo"] == 2331.9
