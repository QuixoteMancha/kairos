"""Synthetic evidence only; no production data, external calls or orders."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from unittest.mock import Mock

from peewee import DataError, SqliteDatabase
import pytest

from database.historical.weather import WeatherCaptureJob, WeatherContract, WeatherEvidence, WeatherStation
from models.fairprice.weather_calibration import calibrate, metrics, walk_forward
from models.fairprice.weather_research import cli_window, evaluate, probability
from outer_apis.scrapers.weather.evidence_capture import Capture, enqueue
from outer_apis.scrapers.weather.nws_observations import samples as nws_samples
from scripts.weather_dataset import build_dataset, quotes
from scripts.weather_strategy_optimizer import WeatherGenome, evolve, simulate


STATION = dict(code="KNYC", timezone="America/New_York", latitude=40.779, longitude=-73.9692)
PREDICATE = dict(strike_type="greater", floor_strike=89)


def sample(index=0):
    day = date(2026, 7, 1) + timedelta(days=index)
    start, end = cli_window(day, STATION["timezone"])
    decision = start - timedelta(hours=2)
    return dict(ticker=f"KXHIGHNY-{day:%y%b%d}-T89".upper(), group=f"KNYC:{day}",
        station_code="KNYC", target_date=str(day), kind="high_temp", horizon="0_12h",
        lead_hours=2., as_of=decision.isoformat(), label_available_at=(end+timedelta(hours=1)).isoformat(),
        model_names=["gfs_seamless"], model_centers_f=[85.], max_hourly_ramp_f=2.,
        disagreement_f=0., observed_bound_f=None, target_f=90., outcome_yes=1,
        raw_predicate=PREDICATE, rounding="nearest_integer", quote_evidence_id=index+1,
        quote_available_at=decision.isoformat(), yes_ask=.50, no_ask=.51,
        yes_size=10., no_size=10., probability_yes=.85)


def forecast(day, available, model="gfs_seamless"):
    start, _ = cli_window(day, STATION["timezone"])
    return dict(id=1, station_code="KNYC", available_at=available, captured_at=available,
        parameters=dict(models=model, timezone="UTC", latitude=STATION["latitude"], longitude=STATION["longitude"]),
        payload=dict(utc_offset_seconds=0, hourly_units={"temperature_2m": "°F"},
            hourly=dict(time=[(start + timedelta(hours=i)).isoformat() for i in range(24)],
                        temperature_2m=[85.] * 24)))


@pytest.fixture
def db():
    models = [WeatherStation, WeatherContract, WeatherEvidence, WeatherCaptureJob]
    database = SqliteDatabase(":memory:")
    with database.bind_ctx(models):
        database.create_tables(models)
        yield database
    database.close()


def insert_evidence(kind, payload, stamp, *, identity="test", **kwargs):
    return WeatherEvidence.create(kind=kind, identity=identity, payload_json=json.dumps(payload),
        captured_at=stamp, available_at=stamp, source_url="https://example.test",
        parameters_json=kwargs.pop("parameters_json", "{}"), sha256=str(WeatherEvidence.select().count()), **kwargs)


def populate(index=0, *, station=None):
    case = sample(index)
    decision = datetime.fromisoformat(case["as_of"]).replace(tzinfo=None)
    label = datetime.fromisoformat(case["label_available_at"]).replace(tzinfo=None)
    station = station or WeatherStation.create(**STATION, raw_json="{}", fetched_at=decision)
    raw = dict(PREDICATE, ticker=case["ticker"], status="active",
        rules_primary="National Weather Service CLINYC", close_time=case["label_available_at"],
        yes_ask=50, no_ask=51)
    contract = WeatherContract.create(ticker=case["ticker"], series="KXHIGHNY", station=station,
        kind="high_temp", target_date=case["target_date"], status="finalized", result="yes",
        raw_json=json.dumps(raw), fetched_at=label)
    insert_evidence("contract", raw, decision, contract=contract, station=station)
    insert_evidence("market_orderbook", {"orderbook_fp": {"yes_dollars": [["0.49", "10.00"]],
        "no_dollars": [["0.50", "10.00"]]}}, decision, contract=contract, station=station)
    fc = forecast(date.fromisoformat(case["target_date"]), decision)
    insert_evidence("forecast_live", fc["payload"], decision, station=station,
                    parameters_json=json.dumps(fc["parameters"]))
    insert_evidence("contract", dict(raw, status="finalized", result="yes", expiration_value="90"),
                    label, contract=contract, station=station)
    return station, contract


def test_dataset_joins_decision_evidence_and_reports_readiness(db):
    populate()
    result = build_dataset(as_of="2026-07-04T00:00:00Z")
    assert result["coverage"]["point_in_time_training_ready_contracts"] == 1
    assert result["coverage"]["independent_station_days"] == 1
    assert result["coverage"]["snapshots_with_depth"] == 1
    row = result["samples"][0]
    assert row["target_f"] == 90 and row["model_centers_f"] == [85]
    assert row["yes_ask"] == .5 and row["yes_size"] == 10
    assert row["label_available_at"] > row["as_of"]
    assert build_dataset(as_of="2026-07-01T04:00:00Z")["samples"] == []


def test_dataset_excludes_unknown_availability_and_changed_rules(db):
    populate()
    row = WeatherEvidence.get(WeatherEvidence.kind == "forecast_live")
    row.available_at = None
    row.save()
    assert build_dataset(as_of="2026-07-04T00:00:00Z")["rejected"]["no_available_station_forecast"] == 1
    row.available_at = row.captured_at
    row.save()
    final = WeatherEvidence.select().where(WeatherEvidence.kind == "contract").order_by(WeatherEvidence.id.desc()).first()
    raw = json.loads(final.payload_json)
    raw["rules_secondary"] = "changed measuring period"
    final.payload_json = json.dumps(raw)
    final.save()
    assert build_dataset(as_of="2026-07-04T00:00:00Z")["rejected"]["rules_changed_since_decision"] == 1


def test_dataset_to_calibration_and_simulator_end_to_end(db):
    station = None
    for i in range(12):
        station, _ = populate(i, station=station)
    dataset = build_dataset(as_of="2026-07-20T00:00:00Z")
    result = walk_forward(dataset["samples"], minimum_days=3)
    assert result["empirical"]["station_days"] > 0
    assert result["empirical"]["brier"] < result["uncorrected"]["brier"]
    trading = simulate(result["predictions"])
    assert trading["n_trades"] > 0
    assert trading["execution_assumptions"]["fills_verified"] is False
    assert all(r["latest_training_label"] < r["as_of"] for r in result["predictions"])
    assert build_dataset(as_of="2026-07-20T00:00:00Z", limit=2)["truncated"] is True


def test_research_cli_runs_and_writes_reviewable_artifact(tmp_path, monkeypatch):
    from scripts.weather_research import main
    dataset = tmp_path / "synthetic.json"
    dataset.write_text(json.dumps(dict(schema_version=1, truncated=False,
        coverage={"synthetic_only": True}, samples=[sample(i) for i in range(30)])))
    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["weather_research", "evaluate", str(dataset), "--out", str(output),
        "--minimum-days", "3", "--fee-coefficient", ".07", "--slippage", ".01", "--evolve"])
    main()
    result = json.loads(output.read_text())
    assert result["probability_comparison"]["empirical"]["station_days"] > 0
    assert result["evolution"]["automatic_promotion"] is False
    dataset.write_text(json.dumps(dict(schema_version=1, truncated=True)))
    with pytest.raises(SystemExit):
        main()


def test_forecast_model_and_surface_capture_stay_one_request(db):
    stamp = datetime(2026, 7, 1)
    WeatherStation.create(**STATION, raw_json="{}", fetched_at=stamp)
    capture = Capture()
    for key, payload in (("forecast:KNYC:ecmwf_ifs025", {"hourly": {"time": ["2026-07-01T00:00"]}}),
                         ("surface:KNYC", {"features": []})):
        enqueue(key)
        capture.get = Mock(return_value=payload)
        capture.step(WeatherCaptureJob.get(WeatherCaptureJob.key == key))
        assert capture.get.call_count == 1
    forecast_row = WeatherEvidence.get(WeatherEvidence.kind == "forecast_live")
    assert json.loads(forecast_row.parameters_json)["models"] == "ecmwf_ifs025"
    assert WeatherEvidence.get(WeatherEvidence.kind == "surface_nws").available_at is not None


def test_book_capture_does_not_poll_closed_contract_or_create_orders(db):
    station, contract = populate()
    enqueue(f"book:{contract.ticker}")
    capture = Capture()
    capture.get = Mock(return_value={"orderbook_fp": {"no_dollars": [["0.50", "1.00"]]}})
    job = WeatherCaptureJob.get(WeatherCaptureJob.key == f"book:{contract.ticker}")
    capture.step(job)
    capture.get.assert_not_called()
    contract.status = "active"
    contract.close_time = datetime.now() + timedelta(days=1)
    contract.save()
    capture.step(job)
    capture.get.assert_called_once()
    latest = WeatherEvidence.select().where(WeatherEvidence.kind == "market_orderbook").order_by(WeatherEvidence.id.desc()).first()
    assert latest.available_at is not None


def test_small_request_budget_rotates_through_all_lanes(db, monkeypatch):
    enqueue("surface:KNYC")
    enqueue("book:example")
    capture, visited = Capture(), []
    def step(job):
        visited.append(job.key)
        job.next_at = datetime.now() + timedelta(days=1)
        job.save()
    monkeypatch.setattr(capture, "step", step)
    monkeypatch.setattr("outer_apis.scrapers.weather.evidence_capture.time.sleep", lambda _: None)
    for _ in range(10):
        capture.run(budget=1)
    assert "surface:KNYC" in visited and "book:example" in visited


def test_long_settlement_explanation_preserved_without_overflow(db):
    explanation = "No numeric settlement available. " * 12
    Capture().market(dict(ticker="KXHIGHMIA-24NOV05-B84.5", status="finalized",
        expiration_value=explanation, result="yes"), "KXHIGHMIA", "archive", "https://example.test", {})
    contract = WeatherContract.get()
    assert contract.expiration_value is None
    assert json.loads(contract.raw_json)["expiration_value"] == explanation
    assert json.loads(WeatherEvidence.get().payload_json)["expiration_value"] == explanation


def test_database_error_retries_job_without_aborting_other_lanes(db, monkeypatch):
    enqueue("priority:bad:archive")
    enqueue("surface:KNYC")
    capture, visited = Capture(), []
    def step(job):
        if job.key.startswith("priority:"):
            raise DataError("value too long for type character varying(255)")
        visited.append(job.key)
        job.next_at = datetime.now() + timedelta(days=1)
        job.save()
    monkeypatch.setattr(capture, "step", step)
    monkeypatch.setattr("outer_apis.scrapers.weather.evidence_capture.time.sleep", lambda _: None)
    result = capture.run(budget=10)
    assert result["failed"] == 1 and "surface:KNYC" in visited
    assert WeatherCaptureJob.get(WeatherCaptureJob.key == "priority:bad:archive").error.startswith("DataError:")


@pytest.mark.parametrize("quality", ["V", "qc:V"])
def test_nws_quality_station_and_units_are_required(quality):
    props = dict(station="https://api.weather.gov/stations/KNYC", timestamp="2026-07-01T15:00:00Z",
                 temperature=dict(value=30, unitCode="wmoUnit:degC", qualityControl=quality))
    evidence = dict(id=1, available_at="2026-07-01T15:01:00Z", captured_at="2026-07-01T15:01:00Z",
                    payload={"features": [{"properties": props}]})
    assert nws_samples(evidence, "KNYC")[0]["temperature_f"] == 86
    assert nws_samples(evidence, "KMDW") == []
    props["temperature"]["qualityControl"] = "qc:X"
    assert nws_samples(evidence, "KNYC") == []


def test_intraday_uses_available_observed_bound_and_remaining_forecast():
    day = date(2026, 7, 1)
    decision = datetime(2026, 7, 1, 15, tzinfo=timezone.utc)
    obs = dict(id=5, station_code="KNYC", observed_at=decision-timedelta(minutes=5),
        available_at=decision, captured_at=decision, temperature_f=95., quality_verified=True)
    args = dict(as_of=decision, sigma_f=2.2, rounding="nearest_integer", observations=[obs])
    row = forecast(day, decision)
    result = evaluate(PREDICATE, STATION, day, "high_temp", [row], **args)
    assert result["probability_yes"] == pytest.approx(1)
    assert result["observed_bound_f"] == 94
    assert result["innovations_f"] == [6]
    assert result["observation_evidence_ids"] == [5]
    obs["available_at"] += timedelta(seconds=1)
    assert evaluate(PREDICATE, STATION, day, "high_temp", [row], **args)["status"] == "abstain"


def test_clamped_distribution_includes_mass_at_observed_extreme():
    p = probability(dict(strike_type="between", floor_strike=90, cap_strike=91), [85], 2,
                    "nearest_integer", observed_bound=90, kind="high_temp")
    assert p > .99
    p = probability(dict(strike_type="less", cap_strike=90), [95], 2,
                    "continuous", observed_bound=89, kind="low_temp")
    assert p == pytest.approx(1)
    point = probability(dict(strike_type="between", floor_strike=90, cap_strike=90), [85], 2,
                        "continuous", observed_bound=90, kind="high_temp")
    assert point > .99


def test_intraday_discards_already_past_forecast_peak():
    day = date(2026, 7, 1)
    decision = datetime(2026, 7, 1, 15, 30, tzinfo=timezone.utc)
    row = forecast(day, decision)
    row["payload"]["hourly"]["temperature_2m"][9] = 150.  # 14 UTC, before observation.
    observation = dict(id=5, station_code="KNYC", observed_at=decision,
        available_at=decision, captured_at=decision, temperature_f=85., quality_verified=True)
    result = evaluate(PREDICATE, STATION, day, "high_temp", [row], as_of=decision,
        sigma_f=2.2, rounding="nearest_integer", observations=[observation])
    assert result["model_centers_f"] == [85.]


def test_short_horizon_model_does_not_block_complete_models():
    day = date(2026, 7, 1)
    decision = datetime(2026, 7, 1, 3, tzinfo=timezone.utc)
    complete, short = forecast(day, decision), forecast(day, decision, "gfs_hrrr")
    short["payload"]["hourly"]["temperature_2m"][-1] = None
    result = evaluate(PREDICATE, STATION, day, "high_temp", [complete, short],
        as_of=decision, sigma_f=2.2, rounding="nearest_integer")
    assert result["status"] == "scenario"
    assert result["model_names"] == ["gfs_seamless"]
    assert result["excluded_models"] == [{"model": "gfs_hrrr", "reason": "missing_hourly_temperature"}]


def test_calibration_deduplicates_brackets_and_rejects_future_labels():
    history = [sample(i) for i in range(8)]
    result = calibrate(sample(10), history, minimum_days=3)
    duplicates = history + [dict(r, ticker=r["ticker"] + "B") for r in history]
    repeated = calibrate(sample(10), duplicates, minimum_days=3)
    assert result == repeated
    assert result["components"][0]["bias_f"] == 5
    late = dict(sample(0), target_f=1000, label_available_at=sample(11)["as_of"])
    assert calibrate(sample(10), history + [late], minimum_days=3) == result
    same_day = dict(sample(10), target_f=1000, label_available_at=sample(0)["as_of"])
    assert calibrate(sample(10), history + [same_day], minimum_days=3) == result


def test_empirical_residuals_keep_asymmetric_tail_and_regime_is_training_only():
    history = [dict(sample(i), target_f=85. if i < 18 else 98., max_hourly_ramp_f=2. if i < 18 else 8.) for i in range(20)]
    result = calibrate(dict(sample(22), max_hourly_ramp_f=9.), history, minimum_days=3, regime=True)
    assert result["regime"] == "rapid_change"
    assert result["probability_yes"] < result["gaussian_probability_yes"]
    assert result["regime_threshold_f"] == 2.
    assert result["out_of_distribution"] is False


def test_metrics_weight_station_days_equally():
    bad = dict(sample(0), probability_yes=0.)
    good = dict(sample(1), probability_yes=1.)
    assert metrics([bad] * 100 + [good])["brier"] == pytest.approx(.5)
    assert metrics([])["brier"] is None


def test_quotes_use_opposing_bid_and_do_not_invent_size():
    assert quotes({"yes_ask": 50, "volume": 10000}, "market_quote")["yes_size"] is None
    book = quotes({"orderbook": {"yes": [[40, 3], [45, 2]], "no": [[50, 4]]}}, "market_orderbook")
    assert book["yes_ask"] == .5 and book["yes_size"] == 4
    assert book["no_ask"] == pytest.approx(.55) and book["no_size"] == 2


def test_simulator_costs_depth_staleness_and_correlated_exposure():
    rows = [sample(0), dict(sample(0), ticker="other_bracket")]
    result = simulate(rows, replace(WeatherGenome(), max_station_day_dollars=.7), slippage=.01)
    assert result["n_trades"] == 1
    assert result["trades"][0]["fee"] == .02
    assert result["pnl"] == pytest.approx(.47)
    assert simulate([dict(sample(), yes_size=None, no_size=None)])["n_trades"] == 0
    stale = dict(sample(), quote_available_at="2026-06-01T00:00:00Z")
    assert simulate([stale])["n_trades"] == 0
    assert simulate([sample()], initial_capital=.1)["n_trades"] == 0
    assert simulate([dict(sample(), probability_yes=.7)], fee_coefficient=.9)["n_trades"] == 0


def test_evolution_is_deterministic_and_holdout_cannot_change_winner():
    rows = [dict(sample(i), calibrated=True, latest_training_label="2026-06-01T00:00:00Z") for i in range(30)]
    args = dict(seed=2, population_size=6, generations=2, minimum_days_per_fold=3)
    first = evolve(rows, **args)
    second = evolve(rows, **args)
    assert first == second
    changed = [dict(r, outcome_yes=0) if r["target_date"] >= first["holdout_start"] else r for r in rows]
    opposite = evolve(changed, **args)
    assert opposite["genome"] == first["genome"]
    assert opposite["fitness"] == first["fitness"]
    assert opposite["holdout"]["pnl"] != first["holdout"]["pnl"]
    assert first["automatic_promotion"] is False
    assert evolve(rows[:2], **args)["status"] == "insufficient_data"
    with pytest.raises(ValueError, match="chronological"):
        evolve([sample()], **args)
