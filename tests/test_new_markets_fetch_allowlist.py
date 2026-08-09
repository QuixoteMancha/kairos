"""Tests de la restricción explícita del usuario (2026-08-01): solo hacer
fetch de Flashscore para partidos NUEVOS detectados vía la API de
Kalshi, no para todos los partidos abiertos en cada scan."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis import MatchHistory
from database.historical.tennis.matchtracking import filter_new_markets
from database.historical.tennis.writer import get_any_match_history, _synthetic_recentform_match_id
from outer_apis.sports.tennis import match_history


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
        MatchHistory.delete().execute()
    match_history.set_fetch_allowlist(None)  # nunca dejar la allowlist puesta entre tests


def _register(market_id):
    TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour="ATP", surface="Hard", player_a_name="A", player_b_name="B", best_of=3,
        close_time="2026-08-01T00:00:00Z", detected_at=datetime.now(timezone.utc),
    )


def test_filter_new_markets_excludes_already_registered():
    _register("KNOWN-1")
    markets = [{"id": "KNOWN-1"}, {"id": "NEW-1"}, {"id": "NEW-2"}]

    result = filter_new_markets(markets)

    assert {m["id"] for m in result} == {"NEW-1", "NEW-2"}


def test_filter_new_markets_fail_closed_returns_all_on_db_error():
    markets = [{"id": "X"}, {"id": "Y"}]
    with patch("database.historical.tennis.matchtracking.init_db_historical", side_effect=Exception("db down")):
        result = filter_new_markets(markets)
    assert result == markets  # ante la duda, refresca de más, no de menos


def test_fetch_and_cache_skips_live_fetch_when_player_not_in_allowlist():
    """El caso real que motivó esto: jugador de un partido YA conocido,
    con datos viejos en DB -- no debe tocar la red."""
    MatchHistory.create(
        match_id=_synthetic_recentform_match_id("Old Match Player", "X", "2026-07-20"),
        player_a_name="Old Match Player", player_b_name="X", match_date="2026-07-20",
        winner_side="a", score="6-4 6-3", source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )
    match_history.set_fetch_allowlist({"Someone Else"})  # este jugador NO está autorizado hoy

    with patch("outer_apis.sports.tennis.match_history.tennisexplorer_player_scraper.get_player_recent_matches") as mock_fetch:
        result = match_history._fetch_and_cache("Old Match Player")

    mock_fetch.assert_not_called()
    assert len(result) == 1
    assert result[0]["oponente"] == "X"  # sirvió el dato viejo, no nada


def test_fetch_and_cache_returns_empty_when_not_in_allowlist_and_no_data_ever():
    match_history.set_fetch_allowlist({"Someone Else"})

    with patch("outer_apis.sports.tennis.match_history.tennisexplorer_player_scraper.get_player_recent_matches") as mock_fetch:
        result = match_history._fetch_and_cache("Never Fetched Player")

    mock_fetch.assert_not_called()
    assert result == []


def test_fetch_and_cache_fetches_live_when_player_in_allowlist():
    match_history.set_fetch_allowlist({"New Match Player"})
    fake_matches = [{"fecha": "2026-08-01", "oponente": "Y", "resultado": "L", "score": "3-6 4-6", "source": "flashscore"}]

    with patch("outer_apis.sports.tennis.match_history.tennisexplorer_player_scraper.get_player_recent_matches",
               return_value=fake_matches) as mock_fetch, patch(
        "outer_apis.sports.tennis.match_history.save_next_match",
    ):
        result = match_history._fetch_and_cache("New Match Player")

    mock_fetch.assert_called_once()
    assert result == fake_matches


def test_fetch_and_cache_unrestricted_when_allowlist_is_none():
    match_history.set_fetch_allowlist(None)  # comportamiento de siempre
    with patch("outer_apis.sports.tennis.match_history.tennisexplorer_player_scraper.get_player_recent_matches",
               return_value=[]) as mock_fetch, patch(
        "outer_apis.sports.tennis.match_history.save_next_match",
    ):
        match_history._fetch_and_cache("Anybody")
    mock_fetch.assert_called_once()
