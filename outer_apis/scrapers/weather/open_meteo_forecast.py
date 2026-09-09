"""Forecast diario point-in-time de Open-Meteo para investigación climática."""
from __future__ import annotations

from datetime import date
from typing import Any
import requests


def daily_forecast(lat: float, lon: float, target: date) -> dict[str, Any] | None:
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max", "timezone": "UTC"},
            timeout=10,
        )
        response.raise_for_status()
        daily = response.json().get("daily", {})
        index = daily.get("time", []).index(target.isoformat())
        return {key: daily[key][index] for key in ("temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max")}
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None
