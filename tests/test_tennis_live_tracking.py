from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.live_tracking import persist_live_snapshots, summarize_points
from database.historical.tennis.models import (
    LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot, Match, ProbabilityTick,
)
from workers.tennis_live_data_worker import _match_date


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (LiveLimitArbDecision, LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
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


# --- wiring de LiveLimitArb dentro de run_once (2026-08-17) ---

def test_run_once_evaluates_live_limit_arb_after_persisting_snapshots():
    """Termina el trabajo que dejó a medias la sesión de Codex: el
    worker debe evaluar LiveLimitArb sobre el corte recién persistido,
    en el mismo ciclo, sin requests extra."""
    from unittest.mock import patch

    from workers.tennis_live_data_worker import run_once
    from database.historical.tennis.models import LiveLimitArbDecision

    match = Match.create(
        market_id="KX-WIRE", ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Fav Player", player_b_name="Dog Player", best_of=3,
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fswire",
        player_a_is_flashscore_p1=True,
    )
    ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc),
        market_price_a=0.55, market_price_b=0.45,
        model_fair_a=0.72, model_fair_b=0.28,
    )
    games = [{"winner_side": 1, "points": ["0:15"], "break_points": []}] * 4
    points_feed = {"set_1": {"games": games}}
    stats_feed = {"match": {
        "Aces": ["5", "1"], "Double Faults": ["1", "3"],
        "Total Points Won": ["55% (30/55)", "45% (25/55)"],
    }}

    with patch(
        "workers.tennis_live_data_worker._has_fresh_market_tick", return_value=True,
    ), patch(
        "workers.tennis_live_data_worker.ensure_match_history_for_scanned_match",
    ), patch(
        "workers.tennis_live_data_worker.get_point_by_point", return_value=points_feed,
    ), patch(
        "workers.tennis_live_data_worker.get_match_statistics", return_value=stats_feed,
    ):
        result = run_once()

    assert result["live_limit_arb_decisions_evaluated"] == 1
    assert LiveLimitArbDecision.select().count() == 1
    decision = LiveLimitArbDecision.get()
    assert decision.match_id == match.id
    assert decision.case == "favourite_undervalued"


def test_run_once_rotates_batch_so_no_match_is_starved_forever():
    """2026-08-25, hallazgo real verificado contra prod (125 partidos
    abiertos, 34/44 ya resueltos a Flashscore quedaban afuera del batch
    SIEMPRE con el `order_by(detected_at.desc()).limit(N)` fijo viejo --
    mismo patrón de starvation que geocoding/tournament 2026-08-22).
    Con 3 partidos elegibles y BATCH_SIZE parcheado a 2, el 3er partido
    debe aparecer en algún ciclo dentro de 2 corridas, no quedar
    bloqueado para siempre por los 2 más nuevos."""
    from unittest.mock import patch

    from workers.tennis_live_data_worker import run_once

    seen_market_ids: set[str] = set()

    def _fake_points(flashscore_id):
        seen_market_ids.add(flashscore_id)
        return {"set_1": {"games": []}}

    for i in range(3):
        m = Match.create(
            market_id=f"KX-ROT-{i}", ticker_a="A", ticker_b="B", tour="ATP",
            player_a_name="A", player_b_name="B", best_of=3,
            detected_at=datetime.now(timezone.utc), flashscore_match_id=f"fs-rot-{i}",
            player_a_is_flashscore_p1=True,
        )
        ProbabilityTick.create(
            match=m, observed_at=datetime.now(timezone.utc), market_price_a=0.5,
        )

    with patch(
        "workers.tennis_live_data_worker._BATCH_SIZE", 2,
    ), patch(
        "workers.tennis_live_data_worker._has_fresh_market_tick", return_value=True,
    ), patch(
        "workers.tennis_live_data_worker.ensure_match_history_for_scanned_match",
    ), patch(
        "workers.tennis_live_data_worker.get_point_by_point", side_effect=_fake_points,
    ), patch(
        "workers.tennis_live_data_worker.get_match_statistics", return_value=None,
    ):
        run_once()
        run_once()

    assert seen_market_ids == {"fs-rot-0", "fs-rot-1", "fs-rot-2"}


def test_run_once_settles_prior_open_decisions_when_match_resolves():
    from unittest.mock import patch

    from workers.tennis_live_data_worker import run_once
    from database.historical.tennis.models import (
        LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot,
    )

    match = Match.create(
        market_id="KX-SETTLE", ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Fav Player", player_b_name="Dog Player", best_of=3,
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fssettle",
        player_a_is_flashscore_p1=True, winner="a",
    )
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc),
        market_price_a=0.55, market_price_b=0.45,
        model_fair_a=0.72, model_fair_b=0.28,
    )
    point = LivePointSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fssettle",
        player_a_is_flashscore_p1=True, observed_at=datetime.now(timezone.utc),
        games_observed=4, raw_point_json="{}",
    )
    stats = LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fssettle",
        player_a_is_flashscore_p1=True, observed_at=datetime.now(timezone.utc),
        raw_stats_json="{}",
    )
    LiveLimitArbDecision.create(
        match=match, probability_tick=tick, point_snapshot=point, stats_snapshot=stats,
        strategy_version="live_limit_arb_v1", decision_at=datetime.now(timezone.utc),
        favourite_side="a", case="favourite_undervalued", market_probability=0.55,
        live_fair_probability=0.75, edge=0.2, performance_state="stable",
        performance_score=0.0, market_leg_side="a", market_leg_price=0.55,
        limit_leg_side="b", limit_leg_price=0.35, expected_locked_return=0.05,
        features_json="{}", status="open",
    )

    # match.winner ya está seteado -> el filtro `Match.winner.is_null()`
    # de run_once lo excluye del lote, pero settle debe correr igual.
    with patch("workers.tennis_live_data_worker._has_fresh_market_tick", return_value=True):
        result = run_once()

    assert result["live_limit_arb_settled"] == 1
    assert LiveLimitArbDecision.get().status == "won"
