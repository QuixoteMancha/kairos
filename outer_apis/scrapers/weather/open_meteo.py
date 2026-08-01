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
