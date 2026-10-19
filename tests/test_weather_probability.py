from datetime import date, datetime, timedelta, timezone
import json

import pytest
from peewee import SqliteDatabase

from database.historical.weather import WeatherContract, WeatherEvidence, WeatherStation
from models.fairprice.weather_research import cli_window, evaluate, probability
from scripts.weather_probability import report


DAY = date(2026, 7, 10)
AS_OF = datetime(2026, 7, 10, 3, tzinfo=timezone.utc)
STATION = dict(code="KNYC", timezone="America/New_York", latitude=40.779, longitude=-73.9692)
RAW = dict(strike_type="greater", floor_strike=90)


def forecast(center=91, model="gfs_seamless"):
    start, _ = cli_window(DAY, STATION["timezone"])
    return dict(id=1, station_code="KNYC", available_at=AS_OF, captured_at=AS_OF,
        parameters=dict(models=model, timezone="UTC", latitude=STATION["latitude"],
                        longitude=STATION["longitude"]),
        payload=dict(utc_offset_seconds=0, hourly_units={"temperature_2m": "°F"},
            hourly=dict(time=[(start + timedelta(hours=i)).isoformat() for i in range(24)],
                        temperature_2m=[center] * 24)))


def run(rows=None, **kwargs):
    args = dict(as_of=AS_OF, sigma_f=2.2, rounding="continuous")
    args.update(kwargs)
    return evaluate(RAW, STATION, DAY, "high_temp", rows or [forecast()], **args)


@pytest.mark.parametrize("day", [date(2026, 3, 8), date(2026, 11, 1), DAY])
def test_cli_day_stays_24_hours_and_standard_time(day):
    start, end = cli_window(day, "America/New_York")
    assert start.hour == 5
    assert end - start == timedelta(hours=24)


def test_gaussian_example_and_complement():
    result = run()
    assert result["status"] == "scenario"
    assert result["probability_yes"] == pytest.approx(0.675281858, abs=1e-8)
    assert result["probability_yes"] + result["probability_no"] == 1
    assert result["calibrated"] is False


def test_integer_buckets_partition_distribution():
    contracts = [dict(strike_type="less", cap_strike=90),
                 dict(strike_type="between", floor_strike=90, cap_strike=91),
                 dict(strike_type="greater", floor_strike=91)]
    assert sum(probability(c, [91], 2.2, "nearest_integer") for c in contracts) == pytest.approx(1)
    assert probability(RAW, [90.5], 2, "nearest_integer") == pytest.approx(.5)
    assert probability(dict(strike_type="greater_or_equal", floor_strike=90),
                       [89.5], 2, "nearest_integer") == pytest.approx(.5)
    assert probability(dict(strike_type="less_or_equal", cap_strike=90),
                       [90.5], 2, "nearest_integer") == pytest.approx(.5)


@pytest.mark.parametrize("field,value", [("available_at", None),
    ("available_at", AS_OF + timedelta(seconds=1)),
    ("captured_at", AS_OF + timedelta(seconds=1)), ("station_code", "KMDW")])
def test_unknown_future_or_wrong_station_excluded(field, value):
    row = forecast()
    row[field] = value
    assert run([row])["reasons"] == ["no_available_station_forecast"]


def test_stale_and_intraday_abstain():
    row = forecast()
    row["available_at"] = AS_OF - timedelta(hours=7)
    assert run([row])["reasons"] == ["stale_forecast"]
    assert run(as_of=AS_OF + timedelta(hours=2))["reasons"] == ["intraday_requires_verified_observations"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_invalid_hour_is_not_silently_dropped(bad):
    row = forecast()
    row["payload"]["hourly"]["temperature_2m"][5] = bad
    assert run([row])["status"] == "abstain"


def test_incomplete_latest_revision_does_not_fall_back():
    old, new = forecast(), forecast()
    old["available_at"] -= timedelta(hours=1)
    new["payload"]["hourly"]["time"].pop()
    new["payload"]["hourly"]["temperature_2m"].pop()
    assert run([old, new])["reasons"] == ["incomplete_station_day"]


def test_revisions_are_not_ensemble_members_and_disagreement_abstains():
    old = forecast(60)
    old["available_at"] -= timedelta(hours=1)
    assert run([old, forecast()])["model_count"] == 1
    result = run([forecast(80), forecast(95, "other")])
    assert result["reasons"] == ["model_disagreement"]
    result = run([forecast(90), forecast(92, "other")])
    assert result["probability_yes"] == pytest.approx(
        (probability(RAW, [90], 2.2, "continuous") +
         probability(RAW, [92], 2.2, "continuous")) / 2)


def test_units_and_duplicate_hours_rejected():
    row = forecast()
    row["payload"]["hourly_units"]["temperature_2m"] = "°C"
    assert run([row])["status"] == "abstain"
    row = forecast()
    row["payload"]["hourly"]["time"][1] = row["payload"]["hourly"]["time"][0]
    assert run([row])["reasons"] == ["invalid_hourly_axis"]


def test_minimum_uses_only_station_day_and_excludes_next_midnight():
    row = forecast(91)
    hourly = row["payload"]["hourly"]
    hourly["temperature_2m"][3] = 89
    start, end = cli_window(DAY, STATION["timezone"])
    hourly["time"].extend([(start - timedelta(hours=1)).isoformat(), end.isoformat()])
    hourly["temperature_2m"].extend([-10, -20])
    result = evaluate(dict(strike_type="less", cap_strike=89), STATION, DAY,
                      "low_temp", [row], as_of=AS_OF, sigma_f=2.2, rounding="continuous")
    assert result["model_centers_f"] == [89]
    assert result["probability_yes"] == pytest.approx(.5)


@pytest.mark.parametrize("sigma", [0, -1, float("nan"), float("inf")])
def test_invalid_uncertainty_abstains(sigma):
    assert run(sigma_f=sigma)["reasons"] == ["invalid_configuration"]


def test_custom_and_reversed_strikes_rejected():
    for raw in [dict(strike_type="custom"),
                dict(strike_type="between", floor_strike=92, cap_strike=90)]:
        with pytest.raises(ValueError):
            probability(raw, [91], 2.2, "nearest_integer")


def test_database_uses_as_of_rules_and_excludes_archive_without_availability():
    models = [WeatherStation, WeatherContract, WeatherEvidence]
    db = SqliteDatabase(":memory:")
    stamp = AS_OF.replace(tzinfo=None)
    with db.bind_ctx(models):
        db.create_tables(models)
        station = WeatherStation.create(**STATION, raw_json="{}", fetched_at=stamp)
        ticker = "KXHIGHNY-26JUL10-T90"
        contract = WeatherContract.create(ticker=ticker, series="KXHIGHNY", status="finalized",
            raw_json='{"result":"yes"}', fetched_at=stamp)
        raw = dict(RAW, rules_primary="National Weather Service CLINYC", status="active",
                   close_time="2026-07-11T05:00:00Z")
        WeatherEvidence.create(kind="contract", identity=ticker, contract=contract, station=station,
            available_at=stamp, captured_at=stamp, source_url="test", parameters_json="{}",
            payload_json=json.dumps(raw), sha256="rules")
        row = forecast()
        evidence = WeatherEvidence.create(kind="forecast_run", identity="run", station=station,
            captured_at=stamp, initialized_at=stamp, source_url="test",
            parameters_json=json.dumps(row["parameters"]), payload_json=json.dumps(row["payload"]),
            sha256="forecast")
        kwargs = dict(as_of=AS_OF, sigma_f=2.2, rounding="continuous")
        assert report(ticker, **kwargs)["reasons"] == ["no_available_station_forecast"]
        evidence.available_at = stamp
        evidence.save()
        assert report(ticker, **kwargs)["status"] == "scenario"
        revision = WeatherEvidence.get(WeatherEvidence.kind == "contract")
        raw["rules_primary"] = "The Weather Company CLINYC"
        revision.payload_json = json.dumps(raw)
        revision.save()
        assert report(ticker, **kwargs)["reasons"] == ["unverified_nws_cli_station"]
    db.close()
