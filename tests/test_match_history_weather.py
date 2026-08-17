"""Tests del poblado de clima para `MatchHistory` -- 2026-08-16, pedido
explícito del usuario ("un worker que haga fetch de los datos
climáticos... de los matches").

Cierra un gap real: `MatchWeather.match_history` existía como FK desde
2026-08-09 pero NINGUNA función la poblaba (todo el path previo escribía
`MatchWeather.match`, o sea `TennisScannedMatch`, el tracker vivo de
Kalshi)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player, Tournament
from database.historical.tennis import TennisMatchWeather as MatchWeather
from outer_apis.sports.tennis.match_weather import (
    populate_weather_for_match_history,
    populate_weather_for_match_history_row,
)
import outer_apis.sports.tennis.match_weather as weather_module


@pytest.fixture(autouse=True)
def clean_db():
    weather_module._match_history_batch_cursor_id = 0
    init_db_historical()
    with db_historical.connection_context():
        MatchWeather.delete().execute()
        MatchHistory.delete().execute()
        Tournament.delete().execute()
        Player.delete().execute()
    yield
    weather_module._match_history_batch_cursor_id = 0
    with db_historical.connection_context():
        MatchWeather.delete().execute()
        MatchHistory.delete().execute()
        Tournament.delete().execute()
        Player.delete().execute()


def _tournament(**overrides) -> Tournament:
    base = {
        "year": 2024, "tournament_name": "Melbourne", "city": "Melbourne",
        "lat": -37.8136, "lon": 144.9631, "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return Tournament.create(**base)


def _match(**overrides) -> MatchHistory:
    base = {
        "match_id": "m1", "match_date": "2024-01-28",
        "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return MatchHistory.create(**base)


_REAL_WEATHER = {"temperature_c": 19.3, "humidity_pct": 76, "wind_kmh": 12.8}


def test_populates_daily_weather_row_for_match_history():
    match = _match(tournament=_tournament())
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=_REAL_WEATHER,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        result = populate_weather_for_match_history_row(match)

    assert result == {"skipped": False, "populated": True}
    row = MatchWeather.get(MatchWeather.match_history == match)
    assert row.phase == "match_day"
    assert row.temperature_c == 19.3
    assert row.humidity_pct == 76
    assert row.wind_kmh == 12.8
    # `match` (TennisScannedMatch) queda NULL -- es el otro vínculo.
    assert row.match is None


def test_idempotent_does_not_duplicate_weather_row():
    match = _match(tournament=_tournament())
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=_REAL_WEATHER,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        populate_weather_for_match_history_row(match)
        second = populate_weather_for_match_history_row(match)

    assert second["skipped"] is True
    assert second["reason"] == "ya tiene clima"
    assert MatchWeather.select().where(MatchWeather.match_history == match).count() == 1


def test_fail_closed_when_open_meteo_has_no_real_data():
    match = _match(tournament=_tournament())
    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=None,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        result = populate_weather_for_match_history_row(match)

    assert result["skipped"] is True
    assert MatchWeather.select().count() == 0, "no debe fabricar clima sin dato real"


def test_fail_closed_without_match_date():
    match = _match(match_date=None, tournament=_tournament())
    result = populate_weather_for_match_history_row(match)
    assert result == {"skipped": True, "reason": "sin match_date"}
    assert MatchWeather.select().count() == 0


def test_fail_closed_when_tournament_location_unknown():
    # Torneo sin lat/lon y con un nombre que no está en la tabla estática.
    match = _match(tournament=_tournament(
        tournament_name="Torneo Inexistente XYZ", lat=None, lon=None,
    ))
    result = populate_weather_for_match_history_row(match)
    assert result["skipped"] is True
    assert result["reason"] == "sede del torneo no resoluble"
    assert MatchWeather.select().count() == 0


def test_falls_back_to_static_location_table_when_tournament_has_no_coords():
    """Si `Tournament.lat/lon` están vacíos pero el nombre SÍ está en la
    tabla estática de sedes, debe resolver igual (no fallar)."""
    match = _match(tournament=_tournament(
        tournament_name="Wimbledon", lat=None, lon=None,
    ))
    with patch(
        "outer_apis.sports.tennis.match_weather.get_location",
        return_value=(51.434, -0.2144),
    ), patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=_REAL_WEATHER,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        result = populate_weather_for_match_history_row(match)

    assert result == {"skipped": False, "populated": True}


def test_batch_respects_limit_and_skips_already_populated():
    tournament = _tournament()
    for i in range(4):
        _match(match_id=f"m{i}", match_date=f"2024-01-2{i+1}", tournament=tournament)

    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=_REAL_WEATHER,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        first = populate_weather_for_match_history(limit=2)
        assert first["processed"] == 2

        # La segunda corrida no debe re-procesar los ya poblados.
        second = populate_weather_for_match_history(limit=10)
        assert second["processed"] == 2

    assert MatchWeather.select().count() == 4


def test_batch_stops_cleanly_when_daily_budget_exhausted():
    from outer_apis.scrapers.weather.open_meteo import DailyBudgetExhausted

    tournament = _tournament()
    for i in range(3):
        _match(match_id=f"m{i}", match_date=f"2024-01-2{i+1}", tournament=tournament)

    with patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep",
        side_effect=DailyBudgetExhausted("cupo agotado"),
    ):
        result = populate_weather_for_match_history(limit=10)

    assert result["budget_exhausted"] is True
    assert MatchWeather.select().count() == 0


def test_unresolvable_first_batch_does_not_block_later_matches():
    unknown = _tournament(
        tournament_name="Torneo Inexistente XYZ", lat=None, lon=None,
    )
    known = _tournament(
        year=2025, tournament_name="Known", lat=-33.45, lon=-70.66,
    )
    _match(match_id="blocked1", tournament=unknown)
    _match(match_id="blocked2", tournament=unknown)
    _match(match_id="reachable", tournament=known)

    with patch(
        "outer_apis.sports.tennis.match_weather.get_location", return_value=None,
    ), patch(
        "outer_apis.sports.tennis.match_weather.open_meteo.get_daily_weather",
        return_value=_REAL_WEATHER,
    ), patch("outer_apis.sports.tennis.match_weather.open_meteo.throttle_sleep"):
        first = populate_weather_for_match_history(limit=2)
        second = populate_weather_for_match_history(limit=2)

    assert first["skipped"] == 2
    assert second["processed"] == 1
    assert MatchWeather.get().match_history_id == MatchHistory.get(
        MatchHistory.match_id == "reachable"
    ).id
