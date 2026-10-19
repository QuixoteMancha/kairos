"""Normalize NWS surface samples without calling them final CLI extrema."""
import math

from models.fairprice.weather_research import utc


def samples(evidence, station_code):
    rows = []
    for feature in evidence["payload"].get("features", []):
        props = feature.get("properties", {})
        temperature = props.get("temperature") or {}
        if (str(props.get("station", "")).rstrip("/").split("/")[-1] != station_code
                or temperature.get("qualityControl") not in {"V", "qc:V"}
                or temperature.get("unitCode") != "wmoUnit:degC"):
            continue
        try:
            value = float(temperature["value"])
            observed = utc(props["timestamp"])
            if not math.isfinite(value):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(dict(id=evidence["id"], station_code=station_code,
            observed_at=observed, temperature_f=value * 9 / 5 + 32,
            available_at=evidence["available_at"], captured_at=evidence["captured_at"],
            quality_verified=True))
    return rows
