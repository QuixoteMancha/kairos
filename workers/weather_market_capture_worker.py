"""Captura mercados climáticos/forecasts para futuros challengers; sin órdenes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import time

from database.db import db_historical, init_db_historical
from database.historical.weather import WeatherMarketSnapshot
from database.historical.workerstate import mark_cycle_failed, mark_cycle_finished, mark_cycle_started, wait_for_startup_schedule
from outer_apis.kalshi.client import KalshiAPI
from outer_apis.scrapers.weather.open_meteo_forecast import daily_forecast

_WORKER = "weather_market_capture_worker"
_INTERVAL = int(os.getenv("WEATHER_MARKET_CAPTURE_INTERVAL_SECONDS", "900"))
_CITIES = {"KXHIGHNY": ("ny", 40.7128, -74.0060), "KXHIGHCHI": ("chi", 41.8781, -87.6298), "KXHIGHAUS": ("aus", 30.2672, -97.7431), "KXHIGHMIA": ("mia", 25.7617, -80.1918), "KXHIGHDEN": ("den", 39.7392, -104.9903), "KXHIGHPHIL": ("phil", 39.9526, -75.1652)}
_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")

def _target_date(ticker: str):
    match = _DATE.search(ticker)
    if not match: return None
    try: return datetime.strptime("20" + "".join(match.groups()), "%Y%b%d").date()
    except ValueError: return None

def run_once() -> dict:
    init_db_historical(); api = KalshiAPI().initialize(); now = datetime.now(timezone.utc); captured = 0
    with db_historical.atomic():
        for series, (city, lat, lon) in _CITIES.items():
            for market in api.get_markets(series_ticker=series):
                raw, target = market["raw"], _target_date(market["ticker"])
                forecast = daily_forecast(lat, lon, target) if target else None
                WeatherMarketSnapshot.create(ticker=market["ticker"], event_ticker=raw.get("event_ticker"), series_ticker=series, city_code=city, contract_kind="high_temp", target_date=target, title=raw.get("title"), yes_ask=market.get("current_a_price"), no_ask=market.get("current_b_price"), close_time=raw.get("close_time"), status=raw.get("status") or "unknown", result=raw.get("result") or None, forecast_json=json.dumps(forecast) if forecast else None, raw_market_json=json.dumps(raw), observed_at=now); captured += 1
    return {"captured": captured, "observed_at": now.isoformat()}

if __name__ == "__main__":
    wait_for_startup_schedule(_WORKER, _INTERVAL, jitter_max_seconds=60)
    while True:
        try:
            mark_cycle_started(_WORKER); mark_cycle_finished(_WORKER, run_once())
        except Exception as exc:
            mark_cycle_failed(_WORKER, exc); print(f"[{_WORKER}] {exc}", flush=True)
        time.sleep(_INTERVAL)
