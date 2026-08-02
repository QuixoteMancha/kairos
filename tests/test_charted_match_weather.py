"""Tests de clima para `ChartedMatch` (2026-08-02, pedido explícito del
usuario). Distinto de `TennisMatchWeather`: un solo snapshot, no 3
fases, porque no hay hora/duración real de partido en Sackmann."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.chartedmatch import ChartedMatch
from outer_apis.sports.tennis.match_weather import (
    populate_weather_for_charted_match, populate_weather_for_all_charted_matches,
    _CHARTED_MATCH_SNAPSHOT_HOUR_UTC,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        ChartedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        ChartedMatch.delete().execute()


def _make_match(match_id="CM1", tournament="Washington", match_date=date(2026, 8, 1)):
    return ChartedMatch.create(
        match_id=match_id, tour="ATP", player_a_name="A", player_b_name="B",
        match_date=match_date, tournament=tournament, round="F", surface="Hard", best_of=3,
    )


def test_skips_unknown_tournament():
    match = _make_match(tournament="Nowhereville")
    result = populate_weather_for_charted_match(match)
    assert result["skipped"] is True
    assert "no reconocido" in result["reason"]


def test_skips_when_already_populated():
    match = _make_match()
    match.weather_fetched_at = datetime.now(timezone.utc)
    match.save()
    result = populate_weather_for_charted_match(match)
    assert result["skipped"] is True
    assert result["reason"] == "ya tiene clima"


def test_populates_single_snapshot_with_real_fetch_mocked():
    match = _make_match()
    fake_weather = {"temperature_c": 28.0, "humidity_pct": 55.0, "wind_kmh": 12.0}
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
        return_value=fake_weather,
    ) as mock_fetch:
        result = populate_weather_for_charted_match(match)

    assert result == {"skipped": False, "populated": True}
    refreshed = ChartedMatch.get(ChartedMatch.match_id == "CM1")
    assert refreshed.temperature_c == 28.0
    assert refreshed.humidity_pct == 55.0
    assert refreshed.wind_kmh == 12.0
    assert refreshed.weather_fetched_at is not None

    called_when = mock_fetch.call_args[0][2]
    assert called_when.hour == _CHARTED_MATCH_SNAPSHOT_HOUR_UTC
    assert called_when.date() == date(2026, 8, 1)


def test_skips_when_weather_fetch_fails():
    match = _make_match()
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at", return_value=None,
    ):
        result = populate_weather_for_charted_match(match)
    assert result["skipped"] is True
    assert "no disponible" in result["reason"]
    refreshed = ChartedMatch.get(ChartedMatch.match_id == "CM1")
    assert refreshed.weather_fetched_at is None  # reintentable despues


def test_populate_all_charted_matches_aggregate():
    _make_match(match_id="CM2", tournament="Washington")
    _make_match(match_id="CM3", tournament="Nowhereville")

    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
        return_value={"temperature_c": 20.0, "humidity_pct": 40.0, "wind_kmh": 5.0},
    ):
        result = populate_weather_for_all_charted_matches()

    assert result["total"] == 2
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert result["skip_reasons"] == {"torneo no reconocido: 'Nowhereville'": 1}
