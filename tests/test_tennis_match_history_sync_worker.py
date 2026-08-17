"""Tests del worker de sync de MatchHistory/ranking/stats a partir de
partidos nuevos de Kalshi -- 2026-08-08, pedido explícito del usuario."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisScannedMatch, MatchHistory, TennisMatchStats, TennisPlayerSyncStatus
from workers.tennis_match_history_sync_worker import (
    run_once, sync_match, fill_missing_stats_for_player,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
        MatchHistory.delete().execute()
        TennisMatchStats.delete().execute()
        TennisPlayerSyncStatus.delete().execute()
    yield
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
        MatchHistory.delete().execute()
        TennisMatchStats.delete().execute()
        TennisPlayerSyncStatus.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _scanned_match(market_id="MKT-1", history_synced_at=None):
    return TennisScannedMatch.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Novak Djokovic", player_b_name="Carlos Alcaraz",
        best_of=3, detected_at=_now(), history_synced_at=history_synced_at,
    )


def test_sync_match_calls_backfill_player_for_both_players():
    match = _scanned_match()
    with patch(
        "workers.tennis_match_history_sync_worker.backfill_player",
        return_value={"status": "ok", "matches_inserted": 3},
    ) as mock_backfill, patch(
        "workers.tennis_match_history_sync_worker.fill_missing_stats_for_player",
        return_value={"attempted": 0, "filled": 0},
    ):
        result = sync_match(match)

    assert mock_backfill.call_count == 2
    called_names = {c.args[0] for c in mock_backfill.call_args_list}
    assert called_names == {"Novak Djokovic", "Carlos Alcaraz"}
    assert result["players"]["player_a"]["history"]["status"] == "ok"
    assert result["players"]["player_b"]["history"]["status"] == "ok"


def test_sync_match_uses_recent_years_only():
    match = _scanned_match()
    with patch(
        "workers.tennis_match_history_sync_worker.backfill_player", return_value={"status": "ok"},
    ) as mock_backfill, patch(
        "workers.tennis_match_history_sync_worker.fill_missing_stats_for_player",
        return_value={"attempted": 0, "filled": 0},
    ):
        sync_match(match)

    for c in mock_backfill.call_args_list:
        years = c.kwargs.get("years") or c.args[1]
        assert len(years) == 2  # año actual + anterior, NO el rango histórico completo de 11 años


def test_sync_match_one_player_error_does_not_block_the_other():
    match = _scanned_match()

    def _side_effect(name, years=None):
        if name == "Novak Djokovic":
            raise RuntimeError("boom")
        return {"status": "ok"}

    with patch(
        "workers.tennis_match_history_sync_worker.backfill_player", side_effect=_side_effect,
    ), patch(
        "workers.tennis_match_history_sync_worker.fill_missing_stats_for_player",
        return_value={"attempted": 0, "filled": 0},
    ):
        result = sync_match(match)

    assert result["players"]["player_a"]["history"]["status"] == "error"
    assert result["players"]["player_b"]["history"]["status"] == "ok"


def test_run_once_marks_matches_as_synced_and_skips_them_next_time():
    _scanned_match(market_id="MKT-1")
    with patch(
        "workers.tennis_match_history_sync_worker.sync_match", return_value={"synced": True},
    ) as mock_sync:
        summary = run_once()
    assert summary["matches_synced"] == 1
    assert mock_sync.call_count == 1

    with db_historical.connection_context():
        row = TennisScannedMatch.get(TennisScannedMatch.market_id == "MKT-1")
    assert row.history_synced_at is not None

    # segunda corrida -- ya sincronizado, no se vuelve a procesar
    with patch("workers.tennis_match_history_sync_worker.sync_match", return_value={"synced": True}) as mock_sync2:
        summary2 = run_once()
    assert summary2["matches_synced"] == 0
    assert mock_sync2.call_count == 0


def test_run_once_only_processes_unsynced_matches():
    _scanned_match(market_id="MKT-OLD", history_synced_at=_now())
    _scanned_match(market_id="MKT-NEW")
    with patch("workers.tennis_match_history_sync_worker.sync_match", return_value={"synced": True}):
        summary = run_once()
    assert summary["matches_synced"] == 1
    assert summary["candidates_found"] == 1


def test_fill_missing_stats_skips_matches_without_score():
    MatchHistory.create(
        match_id="test:1", source="tennisexplorer", match_date="2024-08-10",
        player_a_name="Novak Djokovic", player_b_name="Carlos Alcaraz",
        score=None, fetched_at=_now(),
    )
    with patch("workers.tennis_match_history_sync_worker.find_match_id") as mock_find:
        result = fill_missing_stats_for_player("Novak Djokovic")
    mock_find.assert_not_called()
    assert result == {"attempted": 0, "filled": 0}


def test_fill_missing_stats_respects_limit():
    from database.historical.tennis import Player
    djokovic, _ = Player.get_or_create(
        canonical_name="Novak Djokovic", defaults={"created_at": _now()},
    )
    for i in range(10):
        rival = Player.create(canonical_name=f"Rival {i}", created_at=_now())
        MatchHistory.create(
            match_id=f"test:{i}", source="tennisexplorer", match_date="2024-08-10",
            player_a=djokovic, player_b=rival,
            score="6-4 6-2", fetched_at=_now(),
        )
    with patch("workers.tennis_match_history_sync_worker.find_match_id", return_value=None) as mock_find:
        result = fill_missing_stats_for_player("Novak Djokovic", limit=3)
    assert mock_find.call_count == 3
    assert result["attempted"] == 3


def test_fill_missing_stats_passes_tournament_name_not_fk_model():
    from database.historical.tennis import Player, Tournament

    a = Player.create(canonical_name="Player A", created_at=_now())
    b = Player.create(canonical_name="Player B", created_at=_now())
    tournament = Tournament.create(
        year=2024, tournament_name="Cincinnati", city="Cincinnati", fetched_at=_now(),
    )
    MatchHistory.create(
        match_id="test:tournament", match_date="2024-08-10",
        player_a=a, player_b=b, tournament=tournament,
        score="6-4 6-2", fetched_at=_now(),
    )
    with patch(
        "workers.tennis_match_history_sync_worker.find_match_id", return_value=None,
    ) as find:
        fill_missing_stats_for_player("Player A", limit=1)

    assert find.call_args.args[3] == "Cincinnati"


def test_fill_missing_stats_skips_already_covered_matches():
    mh = MatchHistory.create(
        match_id="test:1", source="tennisexplorer", match_date="2024-08-10",
        player_a_name="Novak Djokovic", player_b_name="Carlos Alcaraz",
        score="6-4 6-2", fetched_at=_now(),
    )
    TennisMatchStats.create(
        match_history=mh.id, source="flashscore", market_id="history:1",
        flashscore_match_id="fs-1", raw_stats_json="{}", fetched_at=_now(),
    )
    with patch("workers.tennis_match_history_sync_worker.find_match_id") as mock_find:
        result = fill_missing_stats_for_player("Novak Djokovic")
    mock_find.assert_not_called()
    assert result == {"attempted": 0, "filled": 0}


# --- cola de prioridad (2026-08-09, pedido explícito del usuario --
# engine.py pide sync urgente para jugadores nuevos, el worker debe
# procesarlos ANTES del barrido normal por partido) ---

def test_sync_player_marks_player_synced():
    from database.historical.tennis import is_player_data_fresh
    with patch(
        "workers.tennis_match_history_sync_worker.backfill_player", return_value={"status": "ok"},
    ), patch(
        "workers.tennis_match_history_sync_worker.fill_missing_stats_for_player",
        return_value={"attempted": 0, "filled": 0},
    ):
        from workers.tennis_match_history_sync_worker import sync_player
        sync_player("Novak Djokovic")
    assert is_player_data_fresh("Novak Djokovic") is True


def test_run_once_processes_priority_queue_before_regular_batch():
    from database.historical.tennis import request_priority_sync

    request_priority_sync("Iga Swiatek")  # nunca apareció en ningún Match, pedido directo

    with patch(
        "workers.tennis_match_history_sync_worker.sync_player", return_value={"synced": True},
    ) as mock_sync_player, patch(
        "workers.tennis_match_history_sync_worker.get_top_rankings",
    ):
        summary = run_once()

    assert summary["priority_synced"] == 1
    mock_sync_player.assert_any_call("Iga Swiatek")


def test_run_once_priority_request_processed_only_once():
    from database.historical.tennis import request_priority_sync, mark_player_synced

    request_priority_sync("Iga Swiatek")
    # `sync_player` real es lo que marca `mark_player_synced` -- el mock
    # tiene que replicar ESE efecto para que la cola avance de verdad.
    with patch(
        "workers.tennis_match_history_sync_worker.sync_player",
        side_effect=lambda n: (mark_player_synced(n) or {"synced": True}),
    ), patch("workers.tennis_match_history_sync_worker.get_top_rankings"):
        run_once()
        summary2 = run_once()
    assert summary2["priority_synced"] == 0


def test_sync_player_failed_history_stays_pending():
    from database.historical.tennis import is_player_data_fresh
    from workers.tennis_match_history_sync_worker import sync_player

    with patch(
        "workers.tennis_match_history_sync_worker.backfill_player",
        return_value={"status": "skipped_no_slug"},
    ), patch(
        "workers.tennis_match_history_sync_worker.fill_missing_stats_for_player",
        return_value={"attempted": 0, "filled": 0},
    ):
        result = sync_player("Unknown Player")

    assert result["synced"] is False
    assert is_player_data_fresh("Unknown Player") is False


def test_run_once_does_not_mark_match_when_a_player_sync_fails():
    _scanned_match(market_id="MKT-RETRY")
    with patch(
        "workers.tennis_match_history_sync_worker.sync_match",
        return_value={"synced": False},
    ):
        summary = run_once()

    row = TennisScannedMatch.get(TennisScannedMatch.market_id == "MKT-RETRY")
    assert summary["matches_synced"] == 0
    assert row.history_synced_at is None
