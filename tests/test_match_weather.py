"""Tests del poblador de clima real por partido (2026-08-01, pedido
explícito del usuario). Mockea toda request HTTP real (Open-Meteo)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.tennismatchweather import TennisMatchWeather
from outer_apis.scrapers.tennis.tournament_location import get_location
from outer_apis.sports.tennis.match_weather import populate_weather_for_match, populate_weather_for_all_resolved_matches


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchWeather.delete().execute()
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchWeather.delete().execute()
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()


def _make_match(market_id, tournament, resolved=True):
    now = datetime.now(timezone.utc)
    m = TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour="ATP", surface="Hard", tournament=tournament,
        player_a_name="A", player_b_name="B", best_of=3,
        close_time="2026-08-01T00:00:00Z", detected_at=now - timedelta(hours=5),
        winner="a" if resolved else None, settled_at=now if resolved else None,
    )
    TennisMatchProbabilityTick.create(match=m, observed_at=now - timedelta(hours=4), market_price_a=0.5, model_fair_a=0.5)
    return m


def test_get_location_known_and_unknown():
    assert get_location("Washington") is not None
    assert get_location("Some Made Up Tournament") is None
    assert get_location(None) is None


def test_populate_weather_skips_unknown_tournament():
    m = _make_match("M1", "Nonexistent Tourney")
    result = populate_weather_for_match(m)
    assert result["skipped"] is True
    assert TennisMatchWeather.select().count() == 0


def test_populate_weather_skips_unresolved_match():
    m = _make_match("M2", "Washington", resolved=False)
    result = populate_weather_for_match(m)
    assert result["skipped"] is True


def test_populate_weather_creates_3_phases_with_real_fetch_mocked():
    m = _make_match("M3", "Washington")
    fake_weather = {"temperature_c": 28.5, "humidity_pct": 65.0, "wind_kmh": 10.2}
    with patch("outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at", return_value=fake_weather) as mock_fetch:
        result = populate_weather_for_match(m)

    assert result["skipped"] is False
    assert set(result["populated_phases"]) == {"before", "during", "end"}
    assert mock_fetch.call_count == 3
    rows = list(TennisMatchWeather.select().where(TennisMatchWeather.match == m))
    assert len(rows) == 3
    assert {r.phase for r in rows} == {"before", "during", "end"}
    assert all(r.temperature_c == 28.5 for r in rows)


def test_populate_weather_is_idempotent():
    m = _make_match("M4", "Washington")
    fake_weather = {"temperature_c": 20.0, "humidity_pct": 50.0, "wind_kmh": 5.0}
    with patch("outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at", return_value=fake_weather) as mock_fetch:
        populate_weather_for_match(m)
        result2 = populate_weather_for_match(m)

    assert result2["populated_phases"] == []  # ya estaban todas, no reintenta
    assert TennisMatchWeather.select().where(TennisMatchWeather.match == m).count() == 3


def test_populate_weather_handles_partial_fetch_failure():
    """Fail-closed por fase: si Open-Meteo falla para una fase puntual,
    se guarda igual con temp/humedad/viento en None, no bloquea las otras."""
    m = _make_match("M5", "Washington")
    with patch("outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at", return_value=None):
        result = populate_weather_for_match(m)

    assert result["skipped"] is False
    rows = list(TennisMatchWeather.select().where(TennisMatchWeather.match == m))
    assert len(rows) == 3
    assert all(r.temperature_c is None for r in rows)


def test_create_weather_row_retries_on_database_locked():
    """Bug real de producción 2026-08-01: contención de escritura con
    otros workers (tick worker/archivador) sobre la misma DB histórica."""
    import peewee
    from outer_apis.sports.tennis.match_weather import _create_weather_row_with_retry

    m = _make_match("MLOCK", "Washington")
    call_count = {"n": 0}
    real_create = TennisMatchWeather.create

    def flaky_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise peewee.OperationalError("database is locked")
        return real_create(**kwargs)

    with patch("outer_apis.sports.tennis.match_weather.TennisMatchWeather.create", side_effect=flaky_create), \
         patch("outer_apis.sports.tennis.match_weather.time.sleep"):
        _create_weather_row_with_retry(
            match=m, phase="before", observed_at=datetime.now(timezone.utc),
            temperature_c=20.0, humidity_pct=50.0, wind_kmh=5.0, fetched_at=datetime.now(timezone.utc),
        )

    assert call_count["n"] == 3
    assert TennisMatchWeather.select().where(TennisMatchWeather.match == m).count() == 1


def test_populate_weather_for_all_resolved_matches_aggregate():
    _make_match("M6", "Washington")
    _make_match("M7", "Unknown Tourney")
    _make_match("M8", "Washington", resolved=False)

    with patch("outer_apis.sports.tennis.match_weather.open_meteo.get_weather_at",
               return_value={"temperature_c": 25.0, "humidity_pct": 40.0, "wind_kmh": 8.0}):
        result = populate_weather_for_all_resolved_matches()

    assert result["total"] == 2  # solo M6 y M7 tienen winner set (M8 no está resuelto)
    assert result["processed"] == 1  # M6
    assert result["skipped"] == 1  # M7 (torneo desconocido)
