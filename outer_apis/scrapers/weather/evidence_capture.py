"""Resumable public evidence capture. Naive datetimes are UTC; never trades.

Archive initialization is NOT public availability. CLI keeps missing/trace flags.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import re
import time

import requests
from peewee import DatabaseError

from database.historical.weather import WeatherCaptureJob, WeatherContract, WeatherEvidence, WeatherStation
from outer_apis.kalshi.client import BASE_URL

NETWORK = "https://mesonet.agron.iastate.edu/geojson/network.php"
CLI = "https://mesonet.agron.iastate.edu/json/cli.py"
FORECAST = "https://api.open-meteo.com/v1/forecast"
RUNS = "https://single-runs-api.open-meteo.com/v1/forecast"
NWS = "https://api.weather.gov/stations"
FORECAST_MODELS = ("gfs_seamless", "ecmwf_ifs025", "gfs_hrrr")
STATIONS = {"KNYC": "CLINYC", "KMDW": "CLIMDW", "KAUS": "CLIAUS",
            "KMIA": "CLIMIA", "KDEN": "CLIDEN", "KPHL": "CLIPHL"}
PRIORITY = {prefix + city for prefix in ("KXHIGH", "KXLOWT")
            for city in ("NY", "CHI", "AUS", "MIA", "DEN", "PHIL")}
MONTHS = dict(zip("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), range(1, 13)))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def target_date(ticker, series=None):
    """Parse only the date token immediately following the known series.

    Other climate contracts often contain strings such as ``-27-01NOV26``;
    treating their strike/category token as a date fabricated 2001/2029
    labels.  A caller without a verified series cannot infer a date.
    """
    if not series or not ticker.startswith(f"{series}-"):
        return None
    match = re.match(r"^(\d{2})([A-Z]{3})(\d{2})(?:-|$)", ticker[len(series) + 1:])
    if match:
        try:
            return date(2000 + int(match[1]), MONTHS[match[2]], int(match[3]))
        except (KeyError, ValueError):
            pass
    return None


def timestamp(value):
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    return None


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def save_evidence(kind, identity, payload, url, params=None, **fields):
    digest = hashlib.sha256(encode({"payload": payload, "params": params or {}, "url": url}).encode()).hexdigest()
    _, created = WeatherEvidence.get_or_create(kind=kind, identity=identity, sha256=digest,
        defaults=dict(payload_json=encode(payload), source_url=url,
                      parameters_json=encode(params or {}), captured_at=utcnow(), **fields))
    return created


def enqueue(key):
    WeatherCaptureJob.get_or_create(key=key, defaults={"next_at": utcnow()})


def settlement_projection(value):
    """Keep only values fitting the legacy scalar projection, without data loss.

    Kalshi sometimes returns long prose in expiration_value (observed: 342
    characters on KXHIGHMIA-24NOV05-B84.5). The complete source value remains in
    raw_json and immutable contract evidence. It is not a numeric weather label.
    """
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= 255 else None


class Capture:
    def __init__(self, start_year=2024, session=None, forecast_models=None):
        self.start_year = start_year
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Kairos weather research (public evidence capture)"})
        configured = forecast_models or os.getenv("WEATHER_FORECAST_MODELS", ",".join(FORECAST_MODELS)).split(",")
        self.forecast_models = tuple(dict.fromkeys(model.strip() for model in configured if model.strip()))
        if not self.forecast_models or any(model not in FORECAST_MODELS for model in self.forecast_models):
            raise ValueError("Unsupported WEATHER_FORECAST_MODELS")

    def get(self, url, params=None):
        response = self.session.get(url, params=params, timeout=(5, 25))
        response.raise_for_status()
        return response.json()

    def step(self, job):
        """One HTTP request; advance cursor only after persisting the entire page."""
        key = job.key.split(":")
        now, delay, cursor = utcnow(), timedelta(days=1), None
        if key[0] == "stations":
            data = self.get(NETWORK, {"network": "NWSCLI"})
            for feature in data["features"]:
                props = feature["properties"]
                code = props.get("sid") or feature.get("id")
                if code not in STATIONS:
                    continue
                lon, lat = feature["geometry"]["coordinates"][:2]
                station, _ = WeatherStation.get_or_create(code=code, defaults=dict(
                    latitude=lat, longitude=lon, timezone=props["tzname"],
                    raw_json=encode(feature), fetched_at=now))
                save_evidence("station_metadata", code, feature, NETWORK,
                              {"network": "NWSCLI"}, station=station)
                for year in range(self.start_year, now.year + 1):
                    enqueue(f"observations:{code}:{year}")
                enqueue(f"forecast:{code}")
                for model in self.forecast_models:
                    if model != "gfs_seamless":
                        enqueue(f"forecast:{code}:{model}")
                enqueue(f"surface:{code}")
                enqueue(f"run:{code}:{now.date() - timedelta(days=1)}")
        elif key[0] == "series":
            url = BASE_URL + "/series"
            params = {"category": "Climate and Weather"}
            for series in self.get(url, params)["series"]:
                ticker = series["ticker"]
                save_evidence("series", ticker, series, url, params, available_at=utcnow())
                for mode in ("open", "settled", "archive"):
                    lane = "priority" if ticker in PRIORITY else "markets"
                    enqueue(f"{lane}:{ticker}:{mode}")
        elif key[0] in ("markets", "priority"):
            series, mode = key[1:]
            url = BASE_URL + ("/historical/markets" if mode == "archive" else "/markets")
            params = {"series_ticker": series, "limit": 100}
            if mode != "archive":
                params["status"] = mode
            if job.cursor:
                params["cursor"] = job.cursor
            data = self.get(url, params)
            for raw in data["markets"]:
                self.market(raw, series, mode, url, params)
            cursor = data.get("cursor") or None
            delay = timedelta(seconds=1 if cursor else (900 if mode == "open" else 86400))
        elif key[0] == "observations":
            code, year = key[1:]
            station = WeatherStation.get(WeatherStation.code == code)
            params = {"station": code, "year": int(year)}
            for row in self.get(CLI, params)["results"]:
                day = date.fromisoformat(row["valid"])
                save_evidence("observation_cli", f"{code}:{day}", row, CLI, params,
                              station=station, target_date=day)
            delay = timedelta(hours=6) if int(year) == now.year else timedelta(days=30)
        elif key[0] == "surface":
            station = WeatherStation.get(WeatherStation.code == key[1])
            url = f"{NWS}/{station.code}/observations"
            # Preserve the response and its quality flags, not an invented daily
            # high/low. Retrieval is the first proven availability of this revision.
            params = {"start": (now - timedelta(hours=30)).isoformat() + "Z", "limit": 500}
            data = self.get(url, params)
            if not isinstance(data.get("features"), list):
                raise ValueError("NWS observation response has no features")
            save_evidence("surface_nws", job.key, data, url, params,
                          station=station, available_at=utcnow())
            delay = timedelta(minutes=15)
        elif key[0] in ("forecast", "run"):
            station = WeatherStation.get(WeatherStation.code == key[1])
            params = dict(latitude=station.latitude, longitude=station.longitude,
                hourly="temperature_2m,precipitation,snowfall,wind_speed_10m,wind_direction_10m,cloud_cover,dew_point_2m",
                models="gfs_seamless", timezone="UTC", temperature_unit="fahrenheit",
                precipitation_unit="inch", wind_speed_unit="mph", forecast_days=3)
            fields = {"station": station}
            if key[0] == "run":
                day = date.fromisoformat(key[2])
                initialized = datetime.combine(day - timedelta(days=1), datetime.min.time())
                params["run"] = initialized.strftime("%Y-%m-%dT%H:%M")
                fields.update(initialized_at=initialized, target_date=day)
                url, kind, delay = RUNS, "forecast_run", timedelta(days=3650)
            else:
                params["models"] = key[2] if len(key) == 3 else "gfs_seamless"
                if params["models"] not in self.forecast_models:
                    job.next_at = now + timedelta(days=1)
                    job.save()
                    return
                # Retain today's early hours for station-day extraction. They
                # remain forecasts, never substituted for station observations.
                params["past_days"] = 1
                url, kind, delay = FORECAST, "forecast_live", timedelta(hours=1)
            data = self.get(url, params)
            if not data.get("hourly", {}).get("time"):
                raise ValueError("Forecast has no hourly time axis")
            if kind == "forecast_live":
                fields["available_at"] = utcnow()
            save_evidence(kind, job.key, data, url, params, **fields)
        elif key[0] == "book":
            contract = WeatherContract.get(WeatherContract.ticker == key[1])
            if contract.status not in {"active", "open"} or (contract.close_time and contract.close_time <= now):
                job.next_at = now + timedelta(days=1)
                job.save()
                return
            url = BASE_URL + f"/markets/{contract.ticker}/orderbook"
            params = {"depth": 1}
            data = self.get(url, params)
            if not any(key in data for key in ("orderbook", "orderbook_fp")):
                raise ValueError("Missing orderbook")
            save_evidence("market_orderbook", f"{contract.ticker}:{now.isoformat()}", data,
                          url, params, contract=contract, station=contract.station,
                          target_date=contract.target_date, available_at=utcnow())
            delay = timedelta(minutes=15)
        elif key[0] == "candles":
            contract = WeatherContract.get(WeatherContract.ticker == key[1])
            raw = json.loads(contract.raw_json)
            start, end = timestamp(raw.get("open_time")), contract.close_time
            if start is None or end is None:
                raise ValueError("No verified candle start/end times")
            start_ts = int(job.cursor) if job.cursor else int(start.replace(tzinfo=timezone.utc).timestamp())
            stop_ts = int(end.replace(tzinfo=timezone.utc).timestamp())
            end_ts = min(start_ts + 7 * 86400, stop_ts)
            path = (f"/historical/markets/{contract.ticker}/candlesticks" if key[2] == "archive"
                    else f"/series/{contract.series}/markets/{contract.ticker}/candlesticks")
            url = BASE_URL + path
            params = dict(start_ts=start_ts, end_ts=end_ts, period_interval=60)
            data = self.get(url, params)
            if "candlesticks" not in data:
                raise ValueError("Missing candlesticks in response")
            save_evidence("market_candles", f"{contract.ticker}:{start_ts}", data, url,
                          params, contract=contract, target_date=contract.target_date)
            cursor = str(end_ts) if end_ts < stop_ts else None
            delay = timedelta(seconds=1) if cursor else timedelta(days=30)
        else:
            raise ValueError(f"Unknown capture job {job.key}")
        job.cursor, job.next_at, job.error = cursor, utcnow() + delay, None
        job.save()

    def market(self, raw, series, mode, url, params):
        now, ticker = utcnow(), raw["ticker"]
        rules = raw.get("rules_primary", "") + " " + raw.get("rules_secondary", "")
        source = ("TWC" if "The Weather Company" in rules else
                  "NWS_CLI" if "National Weather Service" in rules else None)
        station = None
        for code, cli_code in STATIONS.items():
            if re.search(r"\b" + cli_code + r"\b", rules):
                station = WeatherStation.get_or_none(WeatherStation.code == code)
                break
        day = target_date(ticker, series)
        values = dict(series=series, event_ticker=raw.get("event_ticker"), station=station,
            station_match_verified=station is not None and source == "NWS_CLI",
            target_date=day, kind="high_temp" if series.startswith("KXHIGH") else
            "low_temp" if series.startswith("KXLOW") else "unsupported",
            settlement_source=source, status=raw.get("status", "unknown"),
            result=raw.get("result") or None, expiration_value=settlement_projection(raw.get("expiration_value")),
            close_time=timestamp(raw.get("close_time")), raw_json=encode(raw), fetched_at=now)
        contract, created = WeatherContract.get_or_create(ticker=ticker, defaults=values)
        if not created:
            WeatherContract.update(**values).where(WeatherContract.id == contract.id).execute()
        save_evidence("contract", ticker, raw, url, params, contract=contract,
                      station=station, target_date=day, available_at=now)
        if mode == "open":
            save_evidence("market_quote", f"{ticker}:{now.isoformat()}", raw, url, params,
                          contract=contract, station=station, target_date=day, available_at=now)
            if station and day:
                enqueue(f"book:{ticker}")
        if raw.get("result"):
            enqueue(f"candles:{ticker}:{mode}")
        if station and day and date(2026, 4, 3) <= day < now.date():
            enqueue(f"run:{station.code}:{day}")

    def run(self, budget=28):
        enqueue("stations")
        enqueue("series")
        counts = {"completed": 0, "failed": 0, "errors": []}
        lanes = ("stations", "series", "observations:", "forecast:", "run:",
                 "priority:", "markets:", "candles:", "surface:", "book:")
        schedule, _ = WeatherCaptureJob.get_or_create(key="weather_schedule",
            defaults={"next_at": utcnow() + timedelta(days=3650), "cursor": "0"})
        offset = int(schedule.cursor or "0") % len(lanes)
        for index in range(budget):
            job = (WeatherCaptureJob.select().where(
                WeatherCaptureJob.key.startswith(lanes[(offset + index) % len(lanes)]),
                WeatherCaptureJob.next_at <= utcnow()
            ).order_by(WeatherCaptureJob.next_at, WeatherCaptureJob.id).first())
            if job is None:
                continue
            try:
                self.step(job)
                counts["completed"] += 1
            except (requests.RequestException, DatabaseError, ValueError, KeyError, TypeError) as exc:
                job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                # A 4xx means this specific historical run/parameter does not
                # exist. Retrying every hour wastes the mini-PC and keeps a
                # false impression that coverage will eventually appear.
                response = getattr(exc, "response", None)
                terminal = response is not None and 400 <= response.status_code < 500 and response.status_code != 429
                job.next_at = utcnow() + timedelta(days=3650 if terminal else 1 / 24)
                if terminal:
                    job.error = "terminal_source_rejection: " + job.error
                job.save()
                counts["failed"] += 1
                counts["errors"].append({"job": job.key, "error": job.error})
            time.sleep(0.25)
        # Persist lane rotation so a budget smaller than the lane count cannot
        # permanently starve surface observations or books on every cycle.
        schedule.cursor = str((offset + budget) % len(lanes))
        schedule.save()
        return counts
