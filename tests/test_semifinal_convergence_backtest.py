"""Tests del backtest de convergencia en semifinales/finales (2026-08-01,
hipótesis explícita del usuario, caso real motivador: Nakashima vs
Fritz). El gate de validez (fatiga/edad/lesión) se mockea en la mayoría
de los tests -- ver `tests/test_tennis_fatigue_index.py` y
`tests/test_tennis_player_age.py` para la lógica real de esas piezas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical, init_db, db as db_accounting
from database.accounting.limitarbsignal import LimitArbSignal
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from scripts.tennis.semifinal_convergence_backtest import run_backtest

_VALID_GATE = {
    "valid": True, "injury_ok": True, "fatigue_ok": True, "age_ok": True,
    "underdog_fatigue_index": 1.0, "favorite_fatigue_index": 2.0,
    "underdog_age": 22, "favorite_age": 28,
}
_INVALID_GATE = {**_VALID_GATE, "valid": False}


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    init_db()
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    with db_accounting.connection_context():
        LimitArbSignal.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    with db_accounting.connection_context():
        LimitArbSignal.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _make_match(market_id, round_name, winner="a"):
    return TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour="ATP", surface="Hard", tournament="Washington", round=round_name,
        player_a_name="Player A", player_b_name="Player B",
        best_of=3, detected_at=_now(), winner=winner, settled_at=_now(),
    )


def _make_signal(market_id, dog_side, dog_price, raw_edge, avg_vol, created_at):
    return LimitArbSignal.create(
        market_id=market_id, case="favorite_overvalued",
        avg_volatility=avg_vol, raw_edge=raw_edge, price_adjustment=0.1,
        w_edge=0.5, w_volatility=0.5, return_opportunity=0.05,
        created_at=created_at,
        market_leg_side=dog_side, market_leg_price_at_signal=dog_price,
        limit_leg_side="b" if dog_side == "a" else "a", limit_leg_price=0.6,
    )


def _patched_gate(return_value=_VALID_GATE):
    return patch(
        "scripts.tennis.semifinal_convergence_backtest._check_validity_gate",
        return_value=return_value,
    )


def test_ignores_non_sf_final_matches():
    _make_match("M1", "Quarterfinal")
    result = run_backtest()
    assert result["total_settled_sf_final_matches"] == 0
    assert result["evaluable_cases"] == 0


def test_ignores_unsettled_matches():
    TennisScannedMatch.create(
        market_id="M2", ticker_a="M2-A", ticker_b="M2-B", tour="ATP",
        surface="Hard", tournament="Washington", round="Semifinal",
        player_a_name="A", player_b_name="B", best_of=3, detected_at=_now(),
    )
    result = run_backtest()
    assert result["total_settled_sf_final_matches"] == 0


def test_case_insensitive_round_match():
    _make_match("M3", "semifinal")
    result = run_backtest()
    assert result["total_settled_sf_final_matches"] == 1


def test_hit_when_price_converges_past_target():
    match = _make_match("M4", "Semifinal")
    signal_time = _now() - timedelta(hours=2)
    _make_signal("M4", "a", 0.22, 0.10, 0.16, signal_time)
    # target = 0.22 + 0.10 + 0.16/2 = 0.40
    TennisMatchProbabilityTick.create(
        match=match, observed_at=signal_time + timedelta(minutes=30),
        market_price_a=0.42, model_fair_a=0.4,
    )
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 1
    assert result["hits"] == 1
    assert result["cases"][0]["hit"] is True
    assert result["cases"][0]["target"] == pytest.approx(0.40)


def test_miss_when_price_never_converges():
    match = _make_match("M5", "Final")
    signal_time = _now() - timedelta(hours=2)
    _make_signal("M5", "a", 0.22, 0.10, 0.16, signal_time)  # target=0.40
    TennisMatchProbabilityTick.create(
        match=match, observed_at=signal_time + timedelta(minutes=30),
        market_price_a=0.14, model_fair_a=0.4,
    )
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 1
    assert result["hits"] == 0


def test_dog_side_b_uses_complement_approximation():
    match = _make_match("M6", "Semifinal")
    signal_time = _now() - timedelta(hours=1)
    _make_signal("M6", "b", 0.20, 0.05, 0.20, signal_time)
    # target = 0.20 + 0.05 + 0.10 = 0.35 -- dog es side b, tick guarda
    # market_price_a=0.40 -> dog aproximado = 1-0.40 = 0.60 >= 0.35 -> hit
    TennisMatchProbabilityTick.create(
        match=match, observed_at=signal_time + timedelta(minutes=10),
        market_price_a=0.40, model_fair_a=0.5,
    )
    with _patched_gate():
        result = run_backtest()
    assert result["hits"] == 1


def test_excludes_case_when_signal_price_above_40pct():
    _make_match("M9", "Final")
    _make_signal("M9", "a", 0.45, 0.05, 0.10, _now() - timedelta(hours=1))
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 0
    assert result["excluded_signal_price_above_40pct"] == 1


def test_allows_case_when_signal_price_equals_40pct():
    match = _make_match("M11", "Final")
    signal_time = _now() - timedelta(hours=1)
    _make_signal("M11", "a", 0.40, 0.02, 0.10, signal_time)  # target=0.47
    TennisMatchProbabilityTick.create(
        match=match, observed_at=signal_time + timedelta(minutes=10),
        market_price_a=0.50, model_fair_a=0.45,
    )
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 1
    assert result["excluded_signal_price_above_40pct"] == 0


def test_excludes_case_when_target_above_50pct():
    _make_match("M10", "Final")
    # signal_price < 0.40 pasa el primer filtro, pero target = 0.22 + 0.40
    # + 0.30/2 = 0.77 -- por encima de 0.5, se excluye
    _make_signal("M10", "a", 0.22, 0.40, 0.30, _now() - timedelta(hours=1))
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 0
    assert result["excluded_target_above_50pct"] == 1


def test_excludes_case_when_validity_gate_fails():
    _make_match("M12", "Final")
    _make_signal("M12", "a", 0.22, 0.05, 0.10, _now() - timedelta(hours=1))
    with _patched_gate(_INVALID_GATE):
        result = run_backtest()
    assert result["evaluable_cases"] == 0
    assert result["excluded_validity_gate"] == 1


def test_skips_match_without_underdog_signal():
    _make_match("M7", "Final")
    result = run_backtest()
    assert result["total_settled_sf_final_matches"] == 1
    assert result["evaluable_cases"] == 0


def test_skips_match_without_post_signal_ticks():
    _make_match("M8", "Semifinal")
    _make_signal("M8", "a", 0.20, 0.03, 0.2, _now())
    with _patched_gate():
        result = run_backtest()
    assert result["evaluable_cases"] == 0


def test_hit_rate_none_when_no_evaluable_cases():
    result = run_backtest()
    assert result["hit_rate"] is None
