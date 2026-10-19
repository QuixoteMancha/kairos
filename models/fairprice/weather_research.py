"""Research-only station forecast probabilities; never an order signal.

Residual sigma and integer rounding are explicit scenario assumptions, not fitted
parameters. Hourly grid extrema are proxies for station daily extrema.
"""
import math
from datetime import datetime, time, timedelta, timezone
from statistics import mean, pstdev
from zoneinfo import ZoneInfo


def utc(value):
    """Evidence timestamps and Open-Meteo UTC hourly strings may be naive."""
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def cli_window(day, zone):
    local = datetime.combine(day, time(), ZoneInfo(zone))
    standard_offset = local.utcoffset() - local.dst()
    start = datetime.combine(day, time(), timezone(standard_offset)).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def settlement_yes(raw, value):
    """Apply an explicit Kalshi predicate to a settlement value."""
    kind = raw.get("strike_type")
    if kind == "greater":
        return value > float(raw["floor_strike"])
    if kind == "greater_or_equal":
        return value >= float(raw["floor_strike"])
    if kind == "less":
        return value < float(raw["cap_strike"])
    if kind == "less_or_equal":
        return value <= float(raw["cap_strike"])
    if kind == "between":
        return float(raw["floor_strike"]) <= value <= float(raw["cap_strike"])
    raise ValueError("unsupported_strike")


def probability(raw, centers, sigma_f, rounding, *, observed_bound=None, kind=None):
    """Equal-weight Gaussian mixture, optionally rounded to whole Fahrenheit.

    Kalshi between bounds are inclusive. Unknown/custom predicates are rejected.
    """
    extreme_kind = kind
    if rounding not in {"continuous", "nearest_integer"}:
        raise ValueError("unknown_rounding")
    if not math.isfinite(sigma_f) or sigma_f <= 0:
        raise ValueError("invalid_sigma")
    if not centers or not all(math.isfinite(x) for x in centers):
        raise ValueError("invalid_centers")
    kind = raw.get("strike_type")
    lower, upper = -math.inf, math.inf
    if kind in {"greater", "greater_or_equal", "between"}:
        lower = float(raw["floor_strike"])
        if not math.isfinite(lower):
            raise ValueError("invalid_strike")
        if rounding == "nearest_integer":
            lower = (math.floor(lower) + 0.5 if kind == "greater"
                     else math.ceil(lower) - 0.5)
    if kind in {"less", "less_or_equal", "between"}:
        upper = float(raw["cap_strike"])
        if not math.isfinite(upper):
            raise ValueError("invalid_strike")
        if rounding == "nearest_integer":
            upper = (math.ceil(upper) - 0.5 if kind == "less"
                     else math.floor(upper) + 0.5)
    if kind not in {"greater", "greater_or_equal", "less", "less_or_equal", "between"}:
        raise ValueError("unsupported_strike")
    if kind == "between" and float(raw["floor_strike"]) > float(raw["cap_strike"]):
        raise ValueError("reversed_strikes")
    if lower > upper or (lower == upper and observed_bound is None):
        return 0.0
    def cdf(x, center):
        return 0.5 * (1 + math.erf((x - center) / (sigma_f * math.sqrt(2))))
    if observed_bound is None:
        return mean(cdf(upper, center) - cdf(lower, center) for center in centers)
    if not math.isfinite(observed_bound) or extreme_kind not in {"high_temp", "low_temp"}:
        raise ValueError("invalid_observed_bound")
    settled_bound = math.floor(observed_bound + .5) if rounding == "nearest_integer" else observed_bound
    atom_yes = settlement_yes(raw, settled_bound)
    if extreme_kind == "high_temp":
        return mean(max(0., cdf(upper, c) - cdf(max(lower, observed_bound), c)) +
                    cdf(observed_bound, c) * atom_yes for c in centers)
    return mean(max(0., cdf(min(upper, observed_bound), c) - cdf(lower, c)) +
                (1 - cdf(observed_bound, c)) * atom_yes for c in centers)


def evaluate(raw, station, day, kind, forecasts, *, as_of, sigma_f, rounding,
             max_age_hours=6, max_disagreement_f=5, observations=None):
    """Evaluate forecasts with optional provisional intraday station conditioning.

    Inputs are normalized evidence dictionaries. Select one latest revision per
    model before validating coverage, so an incomplete update cannot silently
    fall back to an old model run. Abstention reasons are machine-readable.
    """
    result = {"status": "abstain", "research_only": True, "reasons": []}
    try:
        as_of = utc(as_of)
        if (not all(math.isfinite(x) and x > 0 for x in
                    (sigma_f, max_age_hours, max_disagreement_f))):
            raise ValueError("invalid_configuration")
        if kind not in {"high_temp", "low_temp"}:
            raise ValueError("unsupported_contract")
        start, end = cli_window(day, station["timezone"])
        if as_of >= end:
            raise ValueError("station_day_finished")
        bound, latest_observation = None, None
        observation_ids = []
        if as_of >= start:
            valid = [row for row in (observations or []) if row.get("quality_verified") is True
                     and row.get("station_code") == station["code"]
                     and row.get("available_at") is not None and row.get("captured_at") is not None
                     and max(utc(row["available_at"]), utc(row["captured_at"])) <= as_of
                     and start <= utc(row["observed_at"]) <= as_of
                     and row.get("temperature_f") is not None
                     and math.isfinite(float(row["temperature_f"]))]
            if not valid:
                raise ValueError("intraday_requires_verified_observations")
            latest_observation = max(valid, key=lambda row: utc(row["observed_at"]))
            if as_of - utc(latest_observation["observed_at"]) > timedelta(minutes=90):
                raise ValueError("stale_station_observation")
            # Provisional surface samples can miss the true daily extreme. They
            # only provide a bound, with a 1 F scenario tolerance for precision.
            bound = ((max(float(r["temperature_f"]) for r in valid) - 1.) if kind == "high_temp"
                     else (min(float(r["temperature_f"]) for r in valid) + 1.))
            observation_ids = sorted({r["id"] for r in valid})
        latest = {}
        for row in forecasts:
            if row.get("available_at") is None or row.get("captured_at") is None:
                continue
            available, captured = utc(row["available_at"]), utc(row["captured_at"])
            if max(available, captured) > as_of or row["station_code"] != station["code"]:
                continue
            params = row["parameters"]
            model = params.get("models")
            if not isinstance(model, str) or not model or "," in model:
                continue
            if model not in latest or (available, captured) > latest[model][0]:
                latest[model] = ((available, captured), row)
        if not latest:
            raise ValueError("no_available_station_forecast")
        centers, ids, ramps, innovations = [], [], [], []
        used_models, excluded_models = [], []
        first_hour = max(start, as_of.replace(minute=0, second=0, microsecond=0))
        required = {start + timedelta(hours=i) for i in range(24)
                    if start + timedelta(hours=i) >= first_hour}
        for model, ((available, _), row) in sorted(latest.items()):
            try:
                evidence_id = row["id"]
                if as_of - available > timedelta(hours=max_age_hours):
                    raise ValueError("stale_forecast")
                params, payload = row["parameters"], row["payload"]
                if (params.get("timezone") != "UTC" or
                        params.get("latitude") != station["latitude"] or
                        params.get("longitude") != station["longitude"] or
                        payload.get("utc_offset_seconds") != 0 or
                        payload.get("hourly_units", {}).get("temperature_2m") != "°F"):
                    raise ValueError("unverified_forecast_units_or_location")
                hourly = payload["hourly"]
                times, values = hourly["time"], hourly["temperature_2m"]
                if len(times) != len(values):
                    raise ValueError("invalid_hourly_axis")
                samples = {}
                for timestamp, value in zip(times, values):
                    timestamp = utc(timestamp)
                    if first_hour <= timestamp < end:
                        if timestamp in samples or timestamp not in required:
                            raise ValueError("invalid_hourly_axis")
                        if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
                            raise ValueError("missing_hourly_temperature")
                        samples[timestamp] = float(value)
                if set(samples) != required:
                    raise ValueError("incomplete_station_day")
                ordered = sorted(samples.items())
                ramp = max((abs(b[1] - a[1]) for a, b in zip(ordered, ordered[1:])), default=0.)
                innovation = 0.
                if latest_observation is not None:
                    axis = {utc(t): v for t, v in zip(times, values)}
                    def interpolate(timestamp):
                        hour = timestamp.replace(minute=0, second=0, microsecond=0)
                        fraction = (timestamp - hour).total_seconds()/3600
                        left = axis.get(hour)
                        right = axis.get(hour + timedelta(hours=1)) if fraction else left
                        if left is None or right is None or not all(math.isfinite(float(v)) for v in (left, right)):
                            raise ValueError("missing_forecast_at_observation")
                        return float(left) * (1-fraction) + float(right) * fraction
                    observed_at = utc(latest_observation["observed_at"])
                    reference = interpolate(observed_at)
                    innovation = max(-6., min(6., float(latest_observation["temperature_f"]) - reference))
                    # Past forecast hours must not become fictitious observed highs.
                    samples = {t: v for t, v in samples.items() if t > as_of}
                    samples[as_of] = interpolate(as_of)
                    samples = {t: v + innovation * math.exp(-max(0., (t - observed_at).total_seconds()/3600)/3.)
                               for t, v in samples.items()}
                center = (max if kind == "high_temp" else min)(samples.values())
                if bound is not None:
                    center = (max if kind == "high_temp" else min)(center, bound)
                centers.append(center)
                innovations.append(innovation)
                ids.append(evidence_id)
                ramps.append(ramp)
                used_models.append(model)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                excluded_models.append({"model": model, "reason": str(exc)})
        result["excluded_models"] = excluded_models
        if not centers:
            raise ValueError(excluded_models[0]["reason"] if len(excluded_models) == 1 else "no_usable_forecast")
        spread = pstdev(centers)
        result.update(model_count=len(centers), model_centers_f=centers,
                      model_names=used_models, max_hourly_ramp_f=max(ramps),
                      innovations_f=innovations, observed_bound_f=bound,
                      observation_evidence_ids=observation_ids,
                      mode="intraday" if bound is not None else "preday",
                      disagreement_f=spread, evidence_ids=ids,
                      window_start=start.isoformat(), window_end=end.isoformat())
        if max(centers) - min(centers) > max_disagreement_f:
            raise ValueError("model_disagreement")
        p = probability(raw, centers, sigma_f, rounding, observed_bound=bound, kind=kind)
        result.update(status="scenario", probability_yes=p, probability_no=1-p,
                      sigma_f=sigma_f, rounding=rounding, calibrated=False)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result["reasons"].append(str(exc))
    return result
