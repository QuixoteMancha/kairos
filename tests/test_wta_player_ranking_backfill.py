"""Tests del backfill de ranking semanal real WTA (2026-08-05, pedido
explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis import WtaPlayerRankingHistory
from scripts.tennis.wta_player_ranking_backfill import build_roster, backfill_player_ranking, run_backfill


def setup_function(_fn):
    init_db_historical()
    WtaPlayerRankingHistory.delete().execute()


def test_build_roster_unions_across_years():
    def fake_ranked(at_date, n=300):
        if at_date.startswith("2016"):
            return [{"id": 1, "name": "A", "ranking": 1}]
        return [{"id": 2, "name": "B", "ranking": 1}]

    with patch(
        "scripts.tennis.wta_player_ranking_backfill.wta_scraper.get_ranked_players_at", side_effect=fake_ranked,
    ), patch("scripts.tennis.wta_player_ranking_backfill.time.sleep"):
        roster = build_roster(years=[2016, 2018])
    assert roster == {1: "A", 2: "B"}


def test_backfill_player_ranking_persists_and_replaces():
    fake_history = [{"ranked_at": "2020-01-01", "singles_ranking": 50}, {"ranked_at": "2021-01-01", "singles_ranking": 10}]
    with patch(
        "scripts.tennis.wta_player_ranking_backfill.wta_scraper.get_weekly_ranking_history", return_value=fake_history,
    ):
        n = backfill_player_ranking(999, "Test Player")
    assert n == 2
    assert WtaPlayerRankingHistory.select().count() == 2

    # re-correr reemplaza, no duplica
    with patch(
        "scripts.tennis.wta_player_ranking_backfill.wta_scraper.get_weekly_ranking_history", return_value=fake_history,
    ):
        backfill_player_ranking(999, "Test Player")
    assert WtaPlayerRankingHistory.select().count() == 2


def test_run_backfill_covers_full_roster():
    with patch(
        "scripts.tennis.wta_player_ranking_backfill.build_roster", return_value={1: "A", 2: "B"},
    ), patch(
        "scripts.tennis.wta_player_ranking_backfill.backfill_player_ranking", return_value=5,
    ), patch("scripts.tennis.wta_player_ranking_backfill.time.sleep"):
        result = run_backfill()
    assert result["roster_size"] == 2
    assert result["ok"] == 2
    assert result["total_ranking_entries"] == 10
