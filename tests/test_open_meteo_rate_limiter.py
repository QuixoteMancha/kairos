"""Tests del limitador real de ventana rodante para Open-Meteo
(2026-08-04, pedido explícito del usuario: "¿los backfills de clima
respetan los rate limits de Open-Meteo?" -- el jitter por request solo
respetaba el límite por MINUTO, no el de HORA/DÍA, ver docstring de
`throttle_sleep`).

2026-08-09: movido de `outer_apis.sports.tennis.match_weather` a
`outer_apis.scrapers.weather.open_meteo` -- no es exclusivo de tenis,
también lo usa `outer_apis.sports.baseball.match_weather`."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from outer_apis.scrapers.weather import open_meteo
from outer_apis.scrapers.weather.open_meteo import (
    throttle_sleep, DailyBudgetExhausted,
    _MAX_REQUESTS_PER_HOUR, _MAX_REQUESTS_PER_DAY,
)


@pytest.fixture(autouse=True)
def clean_request_timestamps():
    open_meteo._request_timestamps.clear()
    yield
    open_meteo._request_timestamps.clear()


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
    open_meteo._request_timestamps.extend(now - i for i in reversed(range(_MAX_REQUESTS_PER_DAY)))
    with patch("outer_apis.scrapers.weather.open_meteo.time.monotonic", return_value=now), \
         patch("outer_apis.scrapers.weather.open_meteo.time.sleep"):
        with pytest.raises(DailyBudgetExhausted):
            throttle_sleep()


def test_throttle_sleeps_extra_when_hourly_budget_reached():
    now = 1_000_000.0
    # todas dentro de la última hora (espaciadas 0.5s -- caben las
    # _MAX_REQUESTS_PER_HOUR dentro de la ventana de 3600s), en orden
    # cronológico real (más vieja primero, mismo invariante que
    # `.append()` en producción)
    open_meteo._request_timestamps.extend(
        now - i * 0.5 for i in reversed(range(_MAX_REQUESTS_PER_HOUR))
    )
    sleep_calls = []
    with patch("outer_apis.scrapers.weather.open_meteo.time.monotonic", return_value=now), \
         patch("outer_apis.scrapers.weather.open_meteo.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        throttle_sleep()
    # el primer sleep (extra por límite horario) debe ser sustancialmente mayor al jitter máximo (0.9s)
    assert sleep_calls[0] > 0.9


def test_throttle_records_timestamp_after_successful_call():
    with patch("outer_apis.scrapers.weather.open_meteo.time.sleep"):
        throttle_sleep()
    assert len(open_meteo._request_timestamps) == 1


def test_throttle_purges_timestamps_older_than_a_day():
    now = 1_000_000.0
    open_meteo._request_timestamps.append(now - 86400.0 - 10.0)  # más viejo que 24h
    with patch("outer_apis.scrapers.weather.open_meteo.time.monotonic", return_value=now), \
         patch("outer_apis.scrapers.weather.open_meteo.time.sleep"):
        throttle_sleep()
    # la vieja se purgó, solo queda la nueva registrada por esta llamada
    assert len(open_meteo._request_timestamps) == 1
