"""Tests del histórico real de MLB (2026-08-04, pedido explícito del
usuario tras notar que TeamMatchHistory es solo una ventana rodante de
45 días para Elo en vivo, no un histórico de verdad)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.baseball import MlbGameHistory
from scripts.mlb_local_backfill import backfill_year, run_backfill, _year_has_cache, enrich_game_datetimes
from scripts.mlb_comeback_analysis import collect_games_from_db


def setup_function(_fn):
    init_db_historical()
    MlbGameHistory.delete().execute()


def _fake_linescore(innings_runs):
    innings = [{"num": i + 1, "home_runs": h, "away_runs": a} for i, (h, a) in enumerate(innings_runs)]
    return {"innings": innings, "home_runs_total": sum(h for h, a in innings_runs), "away_runs_total": sum(a for h, a in innings_runs)}


def test_year_has_cache_false_when_empty():
    assert _year_has_cache(2099) is False


def test_backfill_year_persists_games_with_linescore():
    fake_games = [{"date": "2099-06-01", "home_team": "A", "away_team": "B", "home_won": True, "game_pk": 1}]
    ls = _fake_linescore([(1, 0), (0, 0), (0, 1), (0, 0), (2, 0)])
    with patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_games_in_range", return_value=fake_games), \
         patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_linescore", return_value=ls), \
         patch("scripts.mlb_local_backfill.time.sleep"):
        result = backfill_year(2099)
    assert result["games_saved"] == 1
    assert MlbGameHistory.select().count() == 1
    row = MlbGameHistory.get()
    assert row.game_pk == 1
    assert json.loads(row.innings_json)[0]["home_runs"] == 1


def test_backfill_year_is_idempotent_replaces_year():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=True,
        home_runs_final=3, away_runs_final=1, innings_json=None, fetched_at=datetime.now(timezone.utc),
    )
    fake_games = [{"date": "2099-06-02", "home_team": "C", "away_team": "D", "home_won": False, "game_pk": 2}]
    with patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_games_in_range", return_value=fake_games), \
         patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_linescore", return_value=None), \
         patch("scripts.mlb_local_backfill.time.sleep"):
        backfill_year(2099)
    rows = list(MlbGameHistory.select().where(MlbGameHistory.date.startswith("2099")))
    assert len(rows) == 1
    assert rows[0].game_pk == 2  # el partido viejo (game_pk=1) fue reemplazado


def test_run_backfill_skips_cached_years_unless_current():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=True,
        home_runs_final=3, away_runs_final=1, innings_json=None, fetched_at=datetime.now(timezone.utc),
    )
    with patch("scripts.mlb_local_backfill.date") as mock_date, \
         patch("scripts.mlb_local_backfill.backfill_year") as mock_backfill:
        mock_date.today.return_value.year = 2030
        result = run_backfill(2099, 2099, force=False)
    mock_backfill.assert_not_called()
    assert result["years_skipped_cached"] == 1


def test_collect_games_from_db_never_touches_network():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=False,
        home_runs_final=3, away_runs_final=5,
        innings_json=json.dumps([{"num": i + 1, "home_runs": h, "away_runs": a} for i, (h, a) in enumerate([(3, 1), (0, 0), (0, 0), (0, 0), (0, 0)])]),
        fetched_at=datetime.now(timezone.utc),
    )
    with patch("scripts.mlb_comeback_analysis.mlb_stats_scraper.get_games_in_range") as mock_fetch:
        games = collect_games_from_db("2099-01-01", "2099-12-31")
        mock_fetch.assert_not_called()
    assert len(games) == 1
    assert games[0]["home_runs_f5"] == 3
    assert games[0]["away_runs_f5"] == 1


def test_collect_games_from_db_excludes_incomplete_linescore():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=True,
        home_runs_final=1, away_runs_final=0, innings_json=None, fetched_at=datetime.now(timezone.utc),
    )
    games = collect_games_from_db("2099-01-01", "2099-12-31")
    assert games == []


# ── enrich_game_datetimes (2026-08-05, clima real para MLB) ──

def test_enrich_game_datetimes_only_updates_existing_rows_missing_it():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=True,
        home_runs_final=3, away_runs_final=1, innings_json=None, game_datetime_utc=None,
        fetched_at=datetime.now(timezone.utc),
    )
    fake_games = [{
        "date": "2099-06-01", "home_team": "A", "away_team": "B", "home_won": True,
        "game_pk": 1, "game_datetime_utc": "2099-06-01T19:00:00Z",
    }]
    with patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_games_in_range", return_value=fake_games):
        result = enrich_game_datetimes(2099, 2099)
    assert result["rows_updated"] == 1
    row = MlbGameHistory.get(MlbGameHistory.game_pk == 1)
    assert row.game_datetime_utc == "2099-06-01T19:00:00Z"


def test_enrich_game_datetimes_never_overwrites_already_set_value():
    MlbGameHistory.create(
        game_pk=1, date="2099-06-01", home_team="A", away_team="B", home_won=True,
        home_runs_final=3, away_runs_final=1, innings_json=None,
        game_datetime_utc="2099-06-01T19:00:00Z", fetched_at=datetime.now(timezone.utc),
    )
    fake_games = [{
        "date": "2099-06-01", "home_team": "A", "away_team": "B", "home_won": True,
        "game_pk": 1, "game_datetime_utc": "2099-06-01T20:00:00Z",  # distinto, no debe pisar
    }]
    with patch("scripts.mlb_local_backfill.mlb_stats_scraper.get_games_in_range", return_value=fake_games):
        result = enrich_game_datetimes(2099, 2099)
    assert result["rows_updated"] == 0
    row = MlbGameHistory.get(MlbGameHistory.game_pk == 1)
    assert row.game_datetime_utc == "2099-06-01T19:00:00Z"
