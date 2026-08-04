"""Tests de clima para `TennisMatchHistory` (2026-08-03, pedido
explícito del usuario, fase final del backfill masivo top-200 WTA).
Mismo patrón que test_charted_match_weather.py pero con match_date
CharField ("YYYY-MM-DD") en vez de DateField real."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from outer_apis.sports.tennis.match_weather import (
    populate_weather_for_match_history_row, populate_weather_for_all_match_history,
    _CHARTED_MATCH_SNAPSHOT_HOUR_UTC,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().where(TennisMatchHistory.source == "test_weather").execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().where(TennisMatchHistory.source == "test_weather").execute()


def _make_row(opponent="Opp", tournament="Washington", match_date="2026-08-01"):
    return TennisMatchHistory.create(
        player_name="Test Player", opponent_name=opponent, match_date=match_date,
        result="W", score="6-4 6-3", source="test_weather", fetched_at=datetime.now(timezone.utc),
        tournament=tournament,
    )


def test_skips_unknown_tournament():
    row = _make_row(tournament="Nowhereville")
    result = populate_weather_for_match_history_row(row)
    assert result["skipped"] is True
    assert "no reconocido" in result["reason"]


def test_skips_when_already_populated():
    row = _make_row()
    row.weather_fetched_at = datetime.now(timezone.utc)
    row.save()
    result = populate_weather_for_match_history_row(row)
    assert result["skipped"] is True
    assert result["reason"] == "ya tiene clima"


def test_skips_when_match_date_not_parseable():
    row = _make_row()
    row.match_date = "garbage"
    row.save()
    result = populate_weather_for_match_history_row(row)
    assert result["skipped"] is True
    assert "no parseable" in result["reason"]


def test_populates_single_snapshot_with_real_fetch_mocked():
    row = _make_row(match_date="2026-08-01")
    fake_weather = {"temperature_c": 30.0, "humidity_pct": 60.0, "wind_kmh": 10.0}
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
        return_value=fake_weather,
    ) as mock_fetch:
        result = populate_weather_for_match_history_row(row)

    assert result == {"skipped": False, "populated": True}
    refreshed = TennisMatchHistory.get(TennisMatchHistory.id == row.id)
    assert refreshed.temperature_c == 30.0
    assert refreshed.weather_fetched_at is not None

    called_when = mock_fetch.call_args[0][2]
    assert called_when.hour == _CHARTED_MATCH_SNAPSHOT_HOUR_UTC
    assert called_when.year == 2026 and called_when.month == 8 and called_when.day == 1


def test_skips_when_weather_fetch_fails():
    row = _make_row()
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
        return_value=None,
    ):
        result = populate_weather_for_match_history_row(row)
    assert result["skipped"] is True
    assert "no disponible" in result["reason"]


def test_populate_all_match_history_aggregate():
    _make_row(opponent="A", tournament="Washington")
    _make_row(opponent="B", tournament="Nowhereville")
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
        return_value={"temperature_c": 25.0, "humidity_pct": 50.0, "wind_kmh": 5.0},
    ), patch("outer_apis.sports.tennis.match_weather.time.sleep"):
        result = populate_weather_for_all_match_history()
    assert result["total"] == 2
    assert result["processed"] == 1
    assert result["skipped"] == 1
