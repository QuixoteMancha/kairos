"""Inspect coverage or run a bounded capture: python -m scripts.weather_capture_status --capture."""
import argparse
import json

from peewee import fn

from database.historical.weather import WeatherCaptureJob, WeatherContract, WeatherEvidence, WeatherStation
from workers.weather_market_capture_worker import run_once
from scripts.weather_dataset import build_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--readiness-limit", type=int, default=2000,
                        help="Bound checked quote snapshots; truncated coverage is labelled")
    args = parser.parse_args()
    report = {"cycle": run_once()} if args.capture else {}
    report["stations"] = WeatherStation.select().count()
    report["contracts"] = WeatherContract.select().count()
    report["verified_station_contracts"] = WeatherContract.select().where(
        WeatherContract.station_match_verified == True).count()
    # Settlement-labelled means the location/date/result are traceable. It is
    # deliberately NOT called model-ready: historical single-runs have no
    # independently proven publication timestamp and most contracts still lack
    # captured price candles.
    report["settlement_labelled_temperature_contracts"] = WeatherContract.select().where(
        WeatherContract.kind.in_(("high_temp", "low_temp")),
        WeatherContract.station_match_verified == True,
        WeatherContract.target_date.is_null(False),
        WeatherContract.result.is_null(False),
    ).count()
    report["evidence"] = list(WeatherEvidence.select(
        WeatherEvidence.kind, fn.COUNT(WeatherEvidence.id).alias("count"),
        fn.MIN(WeatherEvidence.target_date).alias("first_date"),
        fn.MAX(WeatherEvidence.target_date).alias("last_date")
    ).group_by(WeatherEvidence.kind).dicts())
    report["jobs"] = WeatherCaptureJob.select().count()
    report["retrying_jobs"] = list(WeatherCaptureJob.select(
        WeatherCaptureJob.key, WeatherCaptureJob.error).where(
            WeatherCaptureJob.error.is_null(False),
            ~WeatherCaptureJob.error.startswith("terminal_source_rejection:")).limit(20).dicts())
    report["terminal_source_rejections"] = WeatherCaptureJob.select().where(
        WeatherCaptureJob.error.startswith("terminal_source_rejection:")).count()
    readiness = build_dataset(limit=args.readiness_limit)
    report["point_in_time_training_ready_contracts"] = readiness["coverage"].get("point_in_time_training_ready_contracts", 0)
    report["readiness"] = {key: value for key, value in readiness.items() if key != "samples"}
    report["point_in_time_training_ready_reason"] = (
        "Computed from captured contract rules, forecasts and final numeric settlements. "
        "Research readiness only; rounding is an explicit nearest-integer scenario. "
        "If truncated, counts refer only to checked snapshots."
    )
    print(json.dumps(report, default=str, indent=2))


if __name__ == "__main__":
    main()
