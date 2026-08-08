"""Tests del worker de sync de MatchHistory/ranking/stats a partir de
partidos nuevos de Kalshi -- 2026-08-08, pedido explícito del usuario."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisScannedMatch, MatchHistory, TennisMatchStats
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
    yield
    with db_historical.connection_context():
        TennisScannedMatch.delete().execute()
        MatchHistory.delete().execute()
        TennisMatchStats.delete().execute()


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
    assert result["players"]["player_a"]["status"] == "ok"
    assert result["players"]["player_b"]["status"] == "ok"


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

    assert result["players"]["player_a"]["status"] == "error"
    assert result["players"]["player_b"]["status"] == "ok"


def test_run_once_marks_matches_as_synced_and_skips_them_next_time():
    _scanned_match(market_id="MKT-1")
    with patch(
        "workers.tennis_match_history_sync_worker.sync_match", return_value={},
    ) as mock_sync:
        summary = run_once()
    assert summary["matches_synced"] == 1
    assert mock_sync.call_count == 1

    with db_historical.connection_context():
        row = TennisScannedMatch.get(TennisScannedMatch.market_id == "MKT-1")
    assert row.history_synced_at is not None

    # segunda corrida -- ya sincronizado, no se vuelve a procesar
    with patch("workers.tennis_match_history_sync_worker.sync_match", return_value={}) as mock_sync2:
        summary2 = run_once()
    assert summary2["matches_synced"] == 0
    assert mock_sync2.call_count == 0


def test_run_once_only_processes_unsynced_matches():
    _scanned_match(market_id="MKT-OLD", history_synced_at=_now())
    _scanned_match(market_id="MKT-NEW")
    with patch("workers.tennis_match_history_sync_worker.sync_match", return_value={}):
        summary = run_once()
    assert summary["matches_synced"] == 1
    assert summary["candidates_found"] == 1


def test_fill_missing_stats_skips_matches_without_score():
    MatchHistory.create(
        match_id="sackmann:1", source="sackmann", match_date="2024-08-10",
        player_a_name="Novak Djokovic", player_b_name="Carlos Alcaraz",
        score=None, fetched_at=_now(),
    )
    with patch("workers.tennis_match_history_sync_worker.find_match_id") as mock_find:
        result = fill_missing_stats_for_player("Novak Djokovic")
    mock_find.assert_not_called()
    assert result == {"attempted": 0, "filled": 0}


def test_fill_missing_stats_respects_limit():
    for i in range(10):
        MatchHistory.create(
            match_id=f"sackmann:{i}", source="sackmann", match_date="2024-08-10",
            player_a_name="Novak Djokovic", player_b_name=f"Rival {i}",
            score="6-4 6-2", fetched_at=_now(),
        )
    with patch("workers.tennis_match_history_sync_worker.find_match_id", return_value=None) as mock_find:
        result = fill_missing_stats_for_player("Novak Djokovic", limit=3)
    assert mock_find.call_count == 3
    assert result["attempted"] == 3


def test_fill_missing_stats_skips_already_covered_matches():
    mh = MatchHistory.create(
        match_id="sackmann:1", source="sackmann", match_date="2024-08-10",
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
