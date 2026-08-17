from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.live_tracking import persist_live_snapshots, summarize_points
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, ProbabilityTick,
)
from workers.tennis_live_data_worker import _match_date


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    for model in (LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match):
        model.delete().execute()
    yield
    for model in (LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match):
        model.delete().execute()


def _match():
    return Match.create(
        market_id="KX-LIVE", ticker_a="A", ticker_b="B", tour="WTA",
        surface="Hard", player_a_name="Player A", player_b_name="Player B",
        best_of=3, close_time="2026-08-17T18:00:00Z",
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fs-live",
        player_a_is_flashscore_p1=False,
    )


def test_live_snapshots_link_same_market_tick_and_are_idempotent():
    match = _match()
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc), market_price_a=.42,
    )
    points = {"set_1": {"games": [{
        "winner_side": 1, "points": ["0:15", "15:15"],
        "break_points": [{"point_index": 1}],
    }]}}
    # Feed P1/P2; match A es P2, por lo tanto las columnas deben invertirse.
    stats = {"match": {"Aces": ["2", "7"], "Double Faults": ["4", "1"]}}

    assert persist_live_snapshots(match, points, stats) == {"points": True, "stats": True}
    assert persist_live_snapshots(match, points, stats) == {"points": False, "stats": False}

    point = LivePointSnapshot.get()
    typed = LiveStatsSnapshot.get()
    assert point.probability_tick_id == tick.id
    assert point.match_id == match.id
    assert (point.current_set, point.games_observed, point.points_observed) == (1, 1, 2)
    assert point.break_points_observed == 1
    assert typed.probability_tick_id == tick.id
    assert (typed.aces_a, typed.aces_b) == (7, 2)
    assert LivePointSnapshot.select().count() == 1
    assert LiveStatsSnapshot.select().count() == 1


def test_live_snapshot_requires_probability_tick():
    match = _match()
    result = persist_live_snapshots(match, {"set_1": {"games": []}}, {"match": {}})
    assert result == {"points": False, "stats": False}


def test_point_summary_handles_multiple_sets():
    assert summarize_points({
        "set_1": {"games": [{"points": ["0:15"], "break_points": []}]},
        "set_2": {"games": [{"points": ["15:0", "30:0"], "break_points": [{}]}]},
    }) == {
        "current_set": 2, "games_observed": 2,
        "points_observed": 3, "break_points_observed": 1,
    }


def test_live_worker_prefers_ticker_match_date_over_series_expiration():
    match = _match()
    match.market_id = "KXWTAMATCH-26AUG17TJEAND"
    match.close_time = "2026-08-31T15:00:00Z"
    assert _match_date(match) == "2026-08-17"
