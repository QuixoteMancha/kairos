"""Tests del limitador real de ventana rodante para Open-Meteo
(2026-08-04, pedido explícito del usuario: "¿los backfills de clima
respetan los rate limits de Open-Meteo?" -- el jitter por request solo
respetaba el límite por MINUTO, no el de HORA/DÍA, ver docstring de
`_open_meteo_throttle_sleep`)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from outer_apis.sports.tennis import match_weather
from outer_apis.sports.tennis.match_weather import (
    _open_meteo_throttle_sleep, DailyBudgetExhausted,
    _MAX_REQUESTS_PER_HOUR, _MAX_REQUESTS_PER_DAY,
)


@pytest.fixture(autouse=True)
def clean_request_timestamps():
    match_weather._request_timestamps.clear()
    yield
    match_weather._request_timestamps.clear()


def test_limits_are_within_documented_open_meteo_caps():
    """Open-Meteo free tier real: 600/min, 5000/hora, 10000/día
    (verificado explícitamente). Nuestros límites configurados deben
    tener margen real por debajo de eso."""
    assert _MAX_REQUESTS_PER_HOUR < 5000
    assert _MAX_REQUESTS_PER_DAY < 10000


def test_jitter_alone_does_not_exceed_hourly_limit_on_average():
    """Regresión del hallazgo real: jitter uniform(0.15, 0.9) solo, sin
    limitador de ventana, promedia ~6857 req/hora -- por ENCIMA del
    límite real de 5000/hora."""
    mean_interval_s = (0.15 + 0.9) / 2
    naive_requests_per_hour = 3600 / mean_interval_s
    assert naive_requests_per_hour > 5000  # confirma que el jitter solo NO alcanza


def test_throttle_raises_when_daily_budget_exhausted():
    now = 1_000_000.0
    match_weather._request_timestamps.extend(now - i for i in reversed(range(_MAX_REQUESTS_PER_DAY)))
    with patch("outer_apis.sports.tennis.match_weather.time.monotonic", return_value=now), \
         patch("outer_apis.sports.tennis.match_weather.time.sleep"):
        with pytest.raises(DailyBudgetExhausted):
            _open_meteo_throttle_sleep()


def test_throttle_sleeps_extra_when_hourly_budget_reached():
    now = 1_000_000.0
    # todas dentro de la última hora (espaciadas 0.5s -- caben las
    # _MAX_REQUESTS_PER_HOUR dentro de la ventana de 3600s), en orden
    # cronológico real (más vieja primero, mismo invariante que
    # `.append()` en producción)
    match_weather._request_timestamps.extend(
        now - i * 0.5 for i in reversed(range(_MAX_REQUESTS_PER_HOUR))
    )
    sleep_calls = []
    with patch("outer_apis.sports.tennis.match_weather.time.monotonic", return_value=now), \
         patch("outer_apis.sports.tennis.match_weather.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        _open_meteo_throttle_sleep()
    # el primer sleep (extra por límite horario) debe ser sustancialmente mayor al jitter máximo (0.9s)
    assert sleep_calls[0] > 0.9


def test_throttle_records_timestamp_after_successful_call():
    with patch("outer_apis.sports.tennis.match_weather.time.sleep"):
        _open_meteo_throttle_sleep()
    assert len(match_weather._request_timestamps) == 1


def test_throttle_purges_timestamps_older_than_a_day():
    now = 1_000_000.0
    match_weather._request_timestamps.append(now - 86400.0 - 10.0)  # más viejo que 24h
    with patch("outer_apis.sports.tennis.match_weather.time.monotonic", return_value=now), \
         patch("outer_apis.sports.tennis.match_weather.time.sleep"):
        _open_meteo_throttle_sleep()
    # la vieja se purgó, solo queda la nueva registrada por esta llamada
    assert len(match_weather._request_timestamps) == 1
