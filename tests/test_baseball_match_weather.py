"""Tests de clima real para MLB (2026-08-05, pedido explícito del
usuario: "adapta el backfill de clima de tenis para los otros
deportes que tiene el sistema")."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.baseball import MlbGameHistory
from outer_apis.sports.baseball.match_weather import (
    populate_weather_for_mlb_game, populate_weather_for_all_mlb_games,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MlbGameHistory.delete().execute()
    yield
    with db_historical.connection_context():
        MlbGameHistory.delete().execute()


def _row(game_pk=1, home_team="New York Yankees", game_datetime_utc="2024-06-01T19:07:00Z", weather_fetched_at=None):
    return MlbGameHistory.create(
        game_pk=game_pk, date="2024-06-01", home_team=home_team, away_team="Boston Red Sox",
        home_won=True, home_runs_final=5, away_runs_final=3, innings_json=None,
        game_datetime_utc=game_datetime_utc, fetched_at=datetime.now(timezone.utc),
        weather_fetched_at=weather_fetched_at,
    )


def test_skips_when_already_populated():
    row = _row(weather_fetched_at=datetime.now(timezone.utc))
    result = populate_weather_for_mlb_game(row)
    assert result == {"skipped": True, "reason": "ya tiene clima"}


def test_skips_unknown_team():
    row = _row(home_team="Nowhereville Sluggers")
    result = populate_weather_for_mlb_game(row)
    assert result["skipped"] is True
    assert "no reconocido" in result["reason"]


def test_skips_when_no_game_datetime():
    row = _row(game_datetime_utc=None)
    result = populate_weather_for_mlb_game(row)
    assert result["skipped"] is True
    assert "game_datetime_utc" in result["reason"]


def test_skips_when_game_datetime_not_parseable():
    row = _row(game_datetime_utc="garbage")
    result = populate_weather_for_mlb_game(row)
    assert result["skipped"] is True
    assert "no parseable" in result["reason"]


def test_populates_weather_at_real_game_start_time():
    row = _row(game_datetime_utc="2024-06-01T19:07:00Z")
    fake_weather = {"temperature_c": 22.0, "humidity_pct": 55.0, "wind_kmh": 12.0}
    with patch(
        "outer_apis.sports.baseball.match_weather.open_meteo.get_weather_at",
        return_value=fake_weather,
    ) as mock_fetch:
        result = populate_weather_for_mlb_game(row)

    assert result == {"skipped": False, "populated": True}
    refreshed = MlbGameHistory.get(MlbGameHistory.id == row.id)
    assert refreshed.temperature_c == 22.0
    assert refreshed.weather_fetched_at is not None

    called_when = mock_fetch.call_args[0][2]
    assert called_when.year == 2024 and called_when.month == 6 and called_when.day == 1
    assert called_when.hour == 19 and called_when.minute == 7


def test_skips_when_weather_fetch_fails():
    row = _row()
    with patch(
        "outer_apis.sports.baseball.match_weather.open_meteo.get_weather_at",
        return_value=None,
    ):
        result = populate_weather_for_mlb_game(row)
    assert result["skipped"] is True
    assert "no disponible" in result["reason"]


def test_populate_all_mlb_games_aggregate():
    _row(game_pk=1, home_team="New York Yankees")
    _row(game_pk=2, home_team="Nowhereville Sluggers")
    with patch(
        "outer_apis.sports.baseball.match_weather.open_meteo.get_weather_at",
        return_value={"temperature_c": 20.0, "humidity_pct": 50.0, "wind_kmh": 5.0},
    ), patch("outer_apis.sports.baseball.match_weather._open_meteo_throttle_sleep"):
        result = populate_weather_for_all_mlb_games()
    assert result["total"] == 2
    assert result["processed"] == 1
    assert result["skipped"] == 1
