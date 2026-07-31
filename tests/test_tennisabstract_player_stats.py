"""Tests del scraper per-player de Tennis Abstract (career-splits por
superficie + head-to-head real) y del facade DB-first, frescura POR
JUGADOR. Mockea toda request HTTP real."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisabstractplayerstats import TennisAbstractCareerSplit, TennisAbstractH2H
from outer_apis.scrapers.tennis.tennisabstract_player_scraper import (
    _slug_for_player, _parse_float_pct, _extract_section,
)
from outer_apis.sports.tennis.tennisabstract_player_stats import get_career_win_pct_by_surface, get_h2h_vs


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisAbstractCareerSplit.delete().execute()
        TennisAbstractH2H.delete().execute()
    yield
    with db_historical.connection_context():
        TennisAbstractCareerSplit.delete().execute()
        TennisAbstractH2H.delete().execute()


def test_slug_for_player_strips_spaces_and_accents():
    assert _slug_for_player("Jannik Sinner") == "JannikSinner"
    assert _slug_for_player("Iga Świątek") == "IgaSwiatek"


def test_parse_float_pct():
    assert _parse_float_pct("81.5%") == pytest.approx(0.815)
    assert _parse_float_pct("not a pct") is None


def test_extract_section_finds_boundaries():
    content = '<h1 id="a-h">A section<table>X</table><h1 id="b-h">B section</h1>'
    section = _extract_section(content, "a")
    assert "A section" in section
    assert "B section" not in section


def test_get_career_win_pct_by_surface_none_when_no_surface():
    assert get_career_win_pct_by_surface("Jannik Sinner", surface=None) is None
    assert get_career_win_pct_by_surface("Jannik Sinner", surface="") is None


def test_get_career_win_pct_by_surface_returns_matching_surface_row():
    fake_splits = [
        {"surface": "Hard", "matches": 308, "wins": 251, "losses": 57, "win_pct": 0.815,
         "hold_pct": 0.878, "break_pct": 0.278, "ace_pct": 0.089, "df_pct": 0.025},
        {"surface": "Clay", "matches": 110, "wins": 83, "losses": 27, "win_pct": 0.755,
         "hold_pct": 0.828, "break_pct": 0.335, "ace_pct": 0.05, "df_pct": 0.024},
    ]
    with patch("outer_apis.sports.tennis.tennisabstract_player_stats._fetch_career_splits", return_value=fake_splits):
        result = get_career_win_pct_by_surface("Jannik Sinner", "Clay")
    assert result["win_pct"] == 0.755
    assert result["matches"] == 110


def test_get_h2h_vs_matches_normalized_name():
    fake_h2h = [{"opponent": "Carlos Alcaraz", "meetings": 18, "wins": 7, "losses": 11, "win_pct": 0.389}]
    with patch("outer_apis.sports.tennis.tennisabstract_player_stats._fetch_h2h", return_value=fake_h2h):
        result = get_h2h_vs("Jannik Sinner", "Carlos Alcaraz")
    assert result["meetings"] == 18
    assert result["win_pct"] == 0.389


def test_get_h2h_vs_none_when_opponent_not_frequent():
    fake_h2h = [{"opponent": "Carlos Alcaraz", "meetings": 18, "wins": 7, "losses": 11, "win_pct": 0.389}]
    with patch("outer_apis.sports.tennis.tennisabstract_player_stats._fetch_h2h", return_value=fake_h2h):
        assert get_h2h_vs("Jannik Sinner", "Some Random Qualifier") is None


def test_fetch_career_splits_skips_live_scrape_when_db_fresh_for_this_player():
    """Verificado por conteo de llamadas -- frescura POR JUGADOR, no global."""
    from outer_apis.sports.tennis import tennisabstract_player_stats
    from database.historical.tennis.writer import save_tennisabstract_career_splits

    save_tennisabstract_career_splits("Jannik Sinner", [
        {"surface": "Hard", "matches": 308, "wins": 251, "losses": 57, "win_pct": 0.815,
         "hold_pct": 0.878, "break_pct": 0.278, "ace_pct": 0.089, "df_pct": 0.025},
    ])

    with patch("outer_apis.sports.tennis.tennisabstract_player_stats.tennisabstract_player_scraper.get_career_splits_by_surface") as mock_fetch:
        splits = tennisabstract_player_stats._fetch_career_splits("Jannik Sinner")

    mock_fetch.assert_not_called()
    assert splits[0]["win_pct"] == 0.815

    # Otro jugador SIN data fresca sí debe disparar el fetch -- confirma
    # que la frescura es por jugador, no una bandera global.
    with patch("outer_apis.sports.tennis.tennisabstract_player_stats.tennisabstract_player_scraper.get_career_splits_by_surface",
               return_value=[]) as mock_fetch2:
        tennisabstract_player_stats._fetch_career_splits("Some Other Player")
    mock_fetch2.assert_called_once()
