"""Read archived weather evidence and report an explicit probability scenario."""
import argparse
import json

from database.historical.weather import WeatherContract, WeatherEvidence
from models.fairprice.weather_research import evaluate, utc
from outer_apis.scrapers.weather.evidence_capture import STATIONS, target_date
from outer_apis.scrapers.weather.nws_observations import samples


def report(ticker, *, as_of, sigma_f, rounding, max_disagreement_f=5, training_samples=None,
           minimum_days=20):
    import re

    cutoff = utc(as_of).replace(tzinfo=None)
    contract = WeatherContract.get_or_none(WeatherContract.ticker == ticker)
    abstain = {"ticker": ticker, "status": "abstain", "research_only": True}
    if contract is None:
        return dict(abstain, reasons=["unknown_contract"])
    # Do not use mutable latest rules or eventual settlement in an as-of query.
    revision = (WeatherEvidence.select().where(
        WeatherEvidence.contract == contract, WeatherEvidence.kind == "contract",
        WeatherEvidence.available_at <= cutoff, WeatherEvidence.captured_at <= cutoff,
    ).order_by(WeatherEvidence.available_at.desc(), WeatherEvidence.id.desc()).first())
    if revision is None:
        return dict(abstain, reasons=["no_available_contract_rules"])
    raw = json.loads(revision.payload_json)
    station = revision.station
    rules = (raw.get("rules_primary") or "") + " " + (raw.get("rules_secondary") or "")
    codes = [code for code, cli in STATIONS.items() if re.search(r"\b" + cli + r"\b", rules)]
    if (station is None or codes != [station.code] or
            "National Weather Service" not in rules or "The Weather Company" in rules):
        return dict(abstain, reasons=["unverified_nws_cli_station"])
    series = ticker.split("-", 1)[0]
    day = target_date(ticker, series)
    if day is None:
        return dict(abstain, reasons=["unknown_target_date"])
    close = raw.get("close_time")
    if (raw.get("status") not in {"active", "open"} or raw.get("result") or
            not close or utc(close) <= utc(as_of)):
        return dict(abstain, reasons=["contract_not_open"])
    rows = WeatherEvidence.select().where(
        WeatherEvidence.station == station,
        WeatherEvidence.kind.in_(("forecast_live", "forecast_run")),
        WeatherEvidence.available_at <= cutoff, WeatherEvidence.captured_at <= cutoff,
    ).order_by(WeatherEvidence.available_at.desc(), WeatherEvidence.id.desc())
    forecasts = []
    seen = set()
    for row in rows.iterator():
        params = json.loads(row.parameters_json)
        model = params.get("models")
        if model in seen:
            continue
        seen.add(model)
        forecasts.append(dict(id=row.id, station_code=station.code,
            available_at=row.available_at, captured_at=row.captured_at,
            parameters=params, payload=json.loads(row.payload_json)))
    observations = []
    surface = WeatherEvidence.select().where(
        WeatherEvidence.station == station, WeatherEvidence.kind == "surface_nws",
        WeatherEvidence.available_at <= cutoff, WeatherEvidence.captured_at <= cutoff,
    ).order_by(WeatherEvidence.available_at.desc(), WeatherEvidence.id.desc()).first()
    if surface is not None:
        observations = samples(dict(id=surface.id, payload=json.loads(surface.payload_json),
            available_at=surface.available_at, captured_at=surface.captured_at), station.code)
    result = evaluate(raw, dict(code=station.code, timezone=station.timezone,
        latitude=station.latitude, longitude=station.longitude), day,
        "high_temp" if series.startswith("KXHIGH") else
        "low_temp" if series.startswith("KXLOW") else "unsupported",
        forecasts, as_of=as_of, sigma_f=sigma_f, rounding=rounding,
        observations=observations, max_disagreement_f=max_disagreement_f)
    output = dict(result, ticker=ticker, contract_evidence_id=revision.id,
                station_code=station.code, target_date=day.isoformat(),
                kind="high_temp" if series.startswith("KXHIGH") else "low_temp",
                raw_predicate={key: raw.get(key) for key in ("strike_type", "floor_strike", "cap_strike")},
                as_of=utc(as_of).isoformat())
    if training_samples is not None and result["status"] == "scenario":
        from models.fairprice.weather_calibration import calibrate, horizon_for
        from models.fairprice.weather_research import cli_window
        start, _ = cli_window(day, station.timezone)
        output.update(group=f"{station.code}:{day.isoformat()}",
                      lead_hours=(start-utc(as_of)).total_seconds()/3600)
        output["horizon"] = horizon_for(output["lead_hours"])
        calibrated = calibrate(output, training_samples, minimum_days=minimum_days)
        if calibrated["status"] == "abstain":
            output.pop("probability_yes", None)
            output.pop("probability_no", None)
        output.update(calibrated)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("--as-of", required=True, help="ISO timestamp with UTC offset")
    uncertainty = parser.add_mutually_exclusive_group(required=True)
    uncertainty.add_argument("--sigma-f", type=float,
                        help="Assumed residual standard deviation; not calibrated")
    uncertainty.add_argument("--training-data", help="Complete archived research dataset JSON")
    parser.add_argument("--rounding", required=True, choices=("continuous", "nearest_integer"))
    args = parser.parse_args()
    from datetime import datetime
    try:
        timestamp = datetime.fromisoformat(args.as_of)
    except ValueError:
        parser.error("--as-of must be an ISO timestamp")
    if timestamp.tzinfo is None:
        parser.error("--as-of must include a UTC offset")
    training = None
    if args.training_data:
        from pathlib import Path
        dataset = json.loads(Path(args.training_data).read_text())
        if dataset.get("schema_version") != 1 or dataset.get("truncated"):
            parser.error("requires a complete schema_version=1 training dataset")
        training = dataset["samples"]
    print(json.dumps(report(args.ticker, as_of=args.as_of, sigma_f=args.sigma_f if args.sigma_f is not None else 2.2,
                            rounding=args.rounding, training_samples=training), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
