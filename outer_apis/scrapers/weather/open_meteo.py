"""Open-Meteo — clima histórico REAL (temperatura, humedad, viento), API
pública gratuita, sin clave, verificada en vivo 2026-08-01. Usada para
poblar el clima de partidos de tenis (ver
`outer_apis/sports/tennis/match_weather.py`), pedido explícito del
usuario: temperatura/humedad/viento antes, durante y al final de cada
partido.

Fail-closed: cualquier fallo de red/parseo devuelve None, nunca fabrica
un valor de clima."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
_REQUEST_TIMEOUT_S = 15


def get_weather_at(lat: float, lon: float, when: datetime) -> dict[str, Any] | None:
    """Temperatura/humedad/viento reales en (lat, lon) a la hora más
    cercana a `when` (UTC). Open-Meteo da datos por HORA -- se redondea
    `when` a la hora más cercana antes de buscar en la respuesta.

    Returns:
        {"temperature_c", "humidity_pct", "wind_kmh"}.
        Fail-closed: None si la fecha es muy reciente/futura (Open-Meteo
        archive solo tiene datos ya observados, con un rezago de días),
        si la request falla, o si la hora buscada no aparece en la
        respuesta."""
    date_str = when.date().isoformat()
    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "latitude": lat, "longitude": lon,
                "start_date": date_str, "end_date": date_str,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "timezone": "UTC",
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    hourly = data.get("hourly")
    if not hourly:
        return None

    times = hourly.get("time") or []
    target = when.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
    if target not in times:
        return None
    idx = times.index(target)

    try:
        temp = hourly["temperature_2m"][idx]
        humidity = hourly["relative_humidity_2m"][idx]
        wind = hourly["wind_speed_10m"][idx]
    except (KeyError, IndexError):
        return None

    if temp is None or humidity is None or wind is None:
        return None

    return {"temperature_c": temp, "humidity_pct": humidity, "wind_kmh": wind}


# --- Rate limiter compartido para backfills masivos (2026-08-04,
# hallazgo real: free tier de Open-Meteo es 600/min, 5000/hora,
# 10000/día -- un jitter simple por request no alcanza para respetar
# los 3 límites a la vez) -- 2026-08-09, movido acá desde
# `outer_apis/sports/tennis/match_weather.py` porque también lo usa
# `outer_apis/sports/baseball/match_weather.py`; no es exclusivo de
# ningún deporte, es un límite real de la API de clima.

import random
import time
from collections import deque

_MIN_INTERVAL_S = 0.15  # ~400 req/min, con margen bajo 600/min documentado
_MAX_INTERVAL_S = 0.9   # jitter (no periódico) en vez de pausa fija
_MAX_REQUESTS_PER_HOUR = 4000   # margen real bajo 5000/hora documentado
_MAX_REQUESTS_PER_DAY = 8000    # margen real bajo 10000/día documentado
_HOUR_S = 3600.0
_DAY_S = 86400.0

# timestamps (epoch, float) de requests reales ya hechas -- ventana
# rodante en memoria del proceso (no persiste entre reinicios, pero es
# lo que corresponde para un backfill de una sola corrida larga).
_request_timestamps: deque[float] = deque()


class DailyBudgetExhausted(Exception):
    """Se agotó el cupo diario real de Open-Meteo (con margen) durante
    esta corrida -- el caller debe detener el backfill en vez de seguir
    pegándole a la API y arriesgarse a un 429 real."""


def throttle_sleep() -> None:
    """Jitter base (piso mínimo real de cortesía) + limitador de
    ventana rodante que frena adicionalmente si se acerca al límite
    real por HORA o por DÍA de Open-Meteo."""
    now = time.monotonic()
    while _request_timestamps and now - _request_timestamps[0] > _DAY_S:
        _request_timestamps.popleft()

    in_last_day = len(_request_timestamps)
    if in_last_day >= _MAX_REQUESTS_PER_DAY:
        raise DailyBudgetExhausted(
            f"Cupo diario de Open-Meteo agotado (con margen: {_MAX_REQUESTS_PER_DAY} "
            "de las últimas 24h) -- deteniendo el backfill para esta corrida."
        )

    in_last_hour = sum(1 for t in _request_timestamps if now - t <= _HOUR_S)
    if in_last_hour >= _MAX_REQUESTS_PER_HOUR:
        oldest_in_hour = next(t for t in _request_timestamps if now - t <= _HOUR_S)
        wait_s = _HOUR_S - (now - oldest_in_hour) + 1.0
        time.sleep(max(wait_s, 0.0))

    time.sleep(random.uniform(_MIN_INTERVAL_S, _MAX_INTERVAL_S))
    _request_timestamps.append(time.monotonic())
