"""Tests del geocoding real de la cola larga de torneos ITF/challenger
(2026-08-04, pedido explícito del usuario: "investiga de donde se
pueden sacar más datos de itf/challenger para completar la data")."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis import TennisMatchHistory, TournamentLocationCache
from scripts.geocode_missing_tournament_locations import (
    _geocode, collect_unresolved_city_candidates, run_geocoding,
)


def setup_function(_fn):
    init_db_historical()
    TennisMatchHistory.delete().execute()
    TournamentLocationCache.delete().execute()


def _row(tournament, weather_fetched_at=None):
    return TennisMatchHistory.create(
        player_name="A", opponent_name="B", match_date="2020-01-01", result="W",
        score="6-4 6-3", source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
        tournament=tournament, weather_fetched_at=weather_fetched_at,
    )


def test_collect_unresolved_city_candidates_excludes_rows_with_weather():
    _row("Prostejov challenger")
    _row("Barletta challenger", weather_fetched_at=datetime.now(timezone.utc))  # ya tiene clima -- no cuenta
    candidates = collect_unresolved_city_candidates()
    assert candidates == {"prostejov": 1}


def test_collect_unresolved_city_candidates_excludes_already_resolved_by_static_table():
    _row("Rome WTA")  # ya resoluble por la tabla estática (normalizada) -- no debería aparecer
    candidates = collect_unresolved_city_candidates()
    assert "rome" not in candidates


def test_geocode_fail_closed_on_no_results():
    with patch("scripts.geocode_missing_tournament_locations.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []
        assert _geocode("nowhereville") is None


def test_geocode_fail_closed_on_request_failure():
    import requests
    with patch(
        "scripts.geocode_missing_tournament_locations.requests.get", side_effect=requests.RequestException,
    ):
        assert _geocode("prostejov") is None


def test_geocode_parses_real_result():
    with patch("scripts.geocode_missing_tournament_locations.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [{"lat": "49.4722", "lon": "17.1122"}]
        result = _geocode("prostejov")
    assert result == (49.4722, 17.1122)


def test_run_geocoding_persists_cache_rows_resolved_and_failed():
    _row("Prostejov challenger")
    _row("Nowhereville ITF")
    with patch(
        "scripts.geocode_missing_tournament_locations._geocode",
        side_effect=lambda c: (49.4722, 17.1122) if c == "prostejov" else None,
    ), patch("scripts.geocode_missing_tournament_locations.time.sleep"):
        result = run_geocoding()

    assert result["candidates_total"] == 2
    assert result["resolved"] == 1
    assert result["failed"] == 1

    ok = TournamentLocationCache.get(TournamentLocationCache.city_candidate == "prostejov")
    assert ok.lat == 49.4722
    fail = TournamentLocationCache.get(TournamentLocationCache.city_candidate == "nowhereville")
    assert fail.lat is None


def test_run_geocoding_never_retries_already_cached_candidate():
    _row("Prostejov challenger")
    TournamentLocationCache.create(
        city_candidate="prostejov", lat=None, lon=None, source="nominatim", fetched_at=datetime.now(timezone.utc),
    )
    with patch("scripts.geocode_missing_tournament_locations._geocode") as mock_geocode, \
         patch("scripts.geocode_missing_tournament_locations.time.sleep"):
        result = run_geocoding()
    mock_geocode.assert_not_called()
    assert result["geocoded_this_run"] == 0


def test_run_geocoding_respects_min_rows_threshold():
    _row("Prostejov challenger")  # n=1
    with patch("scripts.geocode_missing_tournament_locations._geocode") as mock_geocode, \
         patch("scripts.geocode_missing_tournament_locations.time.sleep"):
        result = run_geocoding(min_rows=2)
    mock_geocode.assert_not_called()
    assert result["geocoded_this_run"] == 0
