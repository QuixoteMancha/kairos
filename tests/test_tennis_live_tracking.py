from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.live_tracking import persist_live_snapshots, summarize_points
from database.historical.tennis.models import (
    LiveLimitArbDecision, LivePointSnapshot, LiveStatsSnapshot, Match, MatchHistory,
    Player, ProbabilityTick,
)
from workers.tennis_live_data_worker import _match_date


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (
        LiveLimitArbDecision, LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick,
        Match, MatchHistory, Player,
    )
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


def test_ensure_match_history_links_real_row_by_ticker_date_not_series_close():
    """2026-08-28, bug real encontrado auditando por qué 108/175 partidos
    con punto-a-punto en vivo tenían `tournament=None`:
    `ensure_match_history_for_scanned_match` usaba `close_time[:10]` a
    secas (sin preferir el ticker) para buscar el `MatchHistory` real ya
    scrapeado de TennisExplorer -- con `close_time` apuntando a la
    expiración de la serie (semanas después del partido real), la
    búsqueda por jugadores+fecha nunca encontraba la fila real y creaba
    un duplicado sin torneo/superficie/ranking. Fix: reusa
    `resolve_scanned_match_date` (misma lógica de preferencia de ticker
    que ya tenía el worker de live-data, ahora factorizada a un solo
    lugar)."""
    from database.historical.tennis.matchtracking import (
        ensure_match_history_for_scanned_match,
    )
    from database.historical.tennis.models import Player

    player_a = Player.create(canonical_name="Ticker Real", created_at=datetime.now(timezone.utc))
    player_b = Player.create(canonical_name="Ticker Rival", created_at=datetime.now(timezone.utc))
    real_history = MatchHistory.create(
        match_id="te:real-row", source="tennisexplorer", tour="WTA",
        match_date="2026-08-17", tournament=None, surface="Hard",
        player_a=player_a, player_b=player_b, winner=None,
        fetched_at=datetime.now(timezone.utc),
    )

    scanned = Match.create(
        market_id="KXWTAMATCH-26AUG17TJEAND", ticker_a="A", ticker_b="B",
        tour="WTA", player_a_name="Ticker Real", player_b_name="Ticker Rival",
        best_of=3, close_time="2026-08-31T15:00:00Z",
        detected_at=datetime.now(timezone.utc),
    )

    from unittest.mock import patch
    with patch(
        "database.historical.tennis.writer._resolve_player_id",
        side_effect=lambda name, tour: player_a.id if name == "Ticker Real" else player_b.id,
    ), patch(
        "database.historical.tennis.writer._resolve_tournament_id", return_value=None,
    ):
        linked = ensure_match_history_for_scanned_match(scanned)

    assert linked.id == real_history.id
    assert MatchHistory.select().count() == 1


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


