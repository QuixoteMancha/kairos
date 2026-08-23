"""Tests de `scripts/tennis/live_stats_market_reaction.py` -- 2026-08-23,
pedido explícito del usuario: analizar si el mercado reacciona lento/
insuficiente frente a ventajas grandes en las stats+punto a punto en
vivo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import LiveStatsSnapshot, Match, ProbabilityTick
from scripts.tennis.live_stats_market_reaction import collect_events, run_analysis


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    for model in (LiveStatsSnapshot, ProbabilityTick, Match):
        model.delete().execute()
    yield
    for model in (LiveStatsSnapshot, ProbabilityTick, Match):
        model.delete().execute()


def _match(market_id: str) -> Match:
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="WTA",
        surface="Hard", player_a_name="Player A", player_b_name="Player B",
        best_of=3, close_time="2026-08-17T18:00:00Z",
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
    )


def _snapshot(match, minute, price, *, points_a, points_b, breaks_a=0, breaks_b=0):
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        market_price_a=price,
    )
    return LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_stats_json="{}",
        total_points_won_a=points_a, total_points_won_b=points_b,
        break_points_won_a=breaks_a, break_points_won_b=breaks_b,
    )


def test_ignores_matches_below_min_snapshots():
    match = _match("KX-1")
    _snapshot(match, 0, 0.50, points_a=10, points_b=10)
    _snapshot(match, 5, 0.50, points_a=14, points_b=11)  # solo 2 snapshots, mínimo es 3
    assert collect_events() == []


def test_detects_slow_reaction_confirmed_next_window():
    match = _match("KX-2")
    # ventana 1->2: A domina fuerte (4 puntos + 1 quiebre = swing 4+3=7)
    # pero el precio NO se mueve en esa ventana (reacción nula).
    # ventana 2->3: precio recién ahí sube a favor de A (reacción tardía).
    _snapshot(match, 0, 0.50, points_a=10, points_b=10)
    _snapshot(match, 5, 0.50, points_a=14, points_b=11, breaks_a=1)
    _snapshot(match, 10, 0.65, points_a=15, points_b=13, breaks_a=1)  # breaks_a cumulativo, no baja

    events = collect_events()
    assert len(events) == 2
    first = events[0]
    assert first.swing_towards_a == 6.0  # (14-10)-(11-10) + 3*(1-0)
    assert first.contemporaneous_move == 0.0
    assert first.lagged_move == pytest.approx(0.15)

    report = run_analysis(swing_threshold=4.0)
    assert report["n_ventanas_swing_grande"] == 1
    assert report["n_reaccion_tardia_confirmada"] == 1
    assert report["pct_reaccion_tardia_sobre_swing_grande"] == 100.0


def test_fast_reaction_not_counted_as_slow():
    match = _match("KX-3")
    _snapshot(match, 0, 0.50, points_a=10, points_b=10)
    _snapshot(match, 5, 0.68, points_a=14, points_b=11, breaks_a=1)  # precio YA reaccionó
    _snapshot(match, 10, 0.69, points_a=15, points_b=13, breaks_a=1)  # breaks_a cumulativo, no baja

    report = run_analysis(swing_threshold=4.0)
    assert report["n_ventanas_swing_grande"] == 1
    assert report["n_reaccion_tardia_confirmada"] == 0


def test_missing_price_or_stats_excludes_window_fail_closed():
    match = _match("KX-4")
    tick0 = ProbabilityTick.create(match=match, observed_at=datetime.now(timezone.utc), market_price_a=None)
    LiveStatsSnapshot.create(
        match=match, probability_tick=tick0, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False, observed_at=datetime.now(timezone.utc),
        raw_stats_json="{}", total_points_won_a=10, total_points_won_b=10,
        break_points_won_a=0, break_points_won_b=0,
    )
    _snapshot(match, 5, 0.55, points_a=14, points_b=11, breaks_a=1)
    _snapshot(match, 10, 0.60, points_a=15, points_b=13)

    events = collect_events()
    assert len(events) == 1  # la ventana con price_before=None se excluye, no se inventa
