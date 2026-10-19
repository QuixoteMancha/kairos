"""Weather capture tests use isolated SQLite and mocked public APIs."""
from datetime import date, timedelta
import json
from unittest.mock import Mock

import pytest
import requests
from peewee import SqliteDatabase

from database.historical.weather import WeatherCaptureJob, WeatherContract, WeatherEvidence, WeatherStation
from outer_apis.scrapers.weather.evidence_capture import Capture, enqueue, save_evidence, target_date, utcnow

MODELS = [WeatherStation, WeatherContract, WeatherEvidence, WeatherCaptureJob]


@pytest.fixture(autouse=True)
def isolated():
    db = SqliteDatabase(":memory:")
    with db.bind_ctx(MODELS):
        db.create_tables(MODELS)
        yield
    db.close()


def station():
    return WeatherStation.create(code="KNYC", latitude=40.779, longitude=-73.9692,
                                 timezone="America/New_York", raw_json="{}", fetched_at=utcnow())


def test_dates_are_locale_independent_and_fail_closed():
    assert target_date("KXHIGHNY-26SEP07-T84", "KXHIGHNY") == date(2026, 9, 7)
    assert target_date("KXRAIN-26SEP", "KXRAIN") is None
    assert target_date("X-26FEB30-T1", "X") is None
    assert target_date("KXBRECKOPEN-27-01NOV26", "KXBRECKOPEN") is None
    assert target_date("KXHIGHNY-26SEP07-T84") is None


def test_evidence_idempotence_and_revisions():
    assert save_evidence("test", "id", {"high": 80}, "https://example.test")
    assert not save_evidence("test", "id", {"high": 80}, "https://example.test")
    assert save_evidence("test", "id", {"high": 81}, "https://example.test")
    assert WeatherEvidence.select().count() == 2


def test_twc_station_is_not_verified_against_nws():
    station()
    capture = Capture()
    raw = dict(ticker="KXHIGHNY-26SEP07-T84", status="finalized", result="no",
               expiration_value="78.00", rules_primary="CLINYC according to The Weather Company")
    capture.market(raw, "KXHIGHNY", "settled", "https://example.test", {})
    contract = WeatherContract.get()
    assert contract.station.code == "KNYC"
    assert not contract.station_match_verified
    assert contract.settlement_source == "TWC"
    assert contract.result == "no"
    assert WeatherEvidence.select().where(WeatherEvidence.kind == "market_quote").count() == 0


def test_chicago_is_midway_not_ohare():
    WeatherStation.create(code="KMDW", latitude=41.786, longitude=-87.7524,
                          timezone="America/Chicago", raw_json="{}", fetched_at=utcnow())
    Capture().market(dict(ticker="KXHIGHCHI-26SEP09-T87", status="active",
        rules_primary="Chicago (CLIMDW) according to The Weather Company"),
        "KXHIGHCHI", "open", "https://example.test", {})
    assert WeatherContract.get().station.code == "KMDW"


def test_unknown_station_is_never_inferred_from_series():
    station()
    Capture().market(dict(ticker="KXHIGHNY-26SEP09-T87", status="active",
        rules_primary="An unknown location"), "KXHIGHNY", "open", "https://example.test", {})
    assert WeatherContract.get().station is None
    assert not WeatherContract.get().station_match_verified


def test_archive_forecast_never_claims_init_is_availability():
    station()
    enqueue("run:KNYC:2026-09-07")
    capture = Capture()
    capture.get = Mock(return_value={"hourly": {"time": ["2026-09-06T00:00"]}})
    capture.step(WeatherCaptureJob.get())
    row = WeatherEvidence.get()
    assert row.initialized_at.date() == date(2026, 9, 6)
    assert row.available_at is None
    assert json.loads(row.parameters_json)["timezone"] == "UTC"
    assert row.station.latitude == 40.779


def test_live_forecast_availability_is_retrieval_time():
    station()
    enqueue("forecast:KNYC")
    capture = Capture()
    capture.get = Mock(return_value={"hourly": {"time": ["2026-09-09T00:00"]}})
    capture.step(WeatherCaptureJob.get())
    row = WeatherEvidence.get()
    assert row.available_at is not None
    assert row.initialized_at is None


def test_cli_missing_and_trace_values_are_preserved():
    station()
    enqueue("observations:KNYC:2026")
    capture = Capture()
    capture.get = Mock(return_value={"results": [{"valid": "2026-09-01", "high": "M", "precip": "T"}]})
    capture.step(WeatherCaptureJob.get())
    row = WeatherEvidence.get()
    assert json.loads(row.payload_json)["precip"] == "T"
    assert json.loads(row.payload_json)["high"] == "M"
    assert row.available_at is None


def test_market_page_cursor_and_unquoted_settlements():
    enqueue("markets:KXHIGHNY:archive")
    capture = Capture()
    capture.get = Mock(return_value={"markets": [{"ticker": "KXHIGHNY-26JUL08-T83",
                "status": "finalized", "result": "yes"}], "cursor": "next-page"})
    capture.step(WeatherCaptureJob.get())
    assert WeatherContract.get().result == "yes"
    assert WeatherCaptureJob.get(WeatherCaptureJob.key.startswith("markets")).cursor == "next-page"


def test_failure_keeps_cursor_and_other_lanes_progress(monkeypatch):
    enqueue("markets:X:archive")
    job = WeatherCaptureJob.get()
    job.cursor = "unchanged"
    job.save()
    capture = Capture()
    capture.get = Mock(side_effect=requests.Timeout("test timeout"))
    monkeypatch.setattr("outer_apis.scrapers.weather.evidence_capture.time.sleep", lambda _: None)
    report = capture.run(budget=8)
    job = WeatherCaptureJob.get_by_id(job.id)
    assert report["failed"] == 3
    assert job.cursor == "unchanged"
    assert job.next_at > utcnow() + timedelta(minutes=50)


def test_terminal_http_error_is_not_retried_hourly(monkeypatch):
    enqueue("markets:X:archive")
    response = Mock(status_code=400)
    error = requests.HTTPError("bad request", response=response)
    capture = Capture()
    capture.get = Mock(side_effect=error)
    monkeypatch.setattr("outer_apis.scrapers.weather.evidence_capture.time.sleep", lambda _: None)
    capture.run(budget=8)
    job = WeatherCaptureJob.get(WeatherCaptureJob.key == "markets:X:archive")
    assert job.error.startswith("terminal_source_rejection:")
    assert job.next_at > utcnow() + timedelta(days=3600)


def test_candles_are_aggregates_not_quotes():
    capture = Capture()
    capture.market(dict(ticker="X-26SEP07-T1", status="finalized", result="yes",
        open_time="2026-09-06T14:00:00Z", close_time="2026-09-08T05:00:00Z"),
        "X", "archive", "https://example.test", {})
    capture.get = Mock(return_value={"candlesticks": []})
    capture.step(WeatherCaptureJob.get(WeatherCaptureJob.key.startswith("candles:")))
    assert WeatherEvidence.select().where(WeatherEvidence.kind == "market_candles").count() == 1
    assert WeatherEvidence.select().where(WeatherEvidence.kind == "market_quote").count() == 0
