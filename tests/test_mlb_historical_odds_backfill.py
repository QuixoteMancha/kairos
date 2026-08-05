"""Tests del backfill de odds pre-partido reales de MLB (2026-08-05,
pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from database.db import init_db_historical
from database.historical.baseball import MlbHistoricalOdds
from scripts.baseball.mlb_historical_odds_backfill import backfill_year, run_backfill


def setup_function(_fn):
    init_db_historical()
    MlbHistoricalOdds.delete().execute()


def _fake_odds():
    return [{
        "date": "2019-03-20", "home_team": "Oakland Athletics", "away_team": "Seattle Mariners",
        "home_moneyline_open": -127, "home_moneyline_close": -140,
        "away_moneyline_open": 120, "away_moneyline_close": 130,
        "home_implied_prob_open": 0.56, "home_implied_prob_close": 0.58,
        "away_implied_prob_open": 0.45, "away_implied_prob_close": 0.43,
    }]


def test_backfill_year_persists_rows():
    with patch(
        "scripts.baseball.mlb_historical_odds_backfill.scraper.get_year_odds", return_value=_fake_odds(),
    ):
        result = backfill_year(2019)
    assert result["inserted"] == 1
    assert MlbHistoricalOdds.select().count() == 1
    row = MlbHistoricalOdds.get()
    assert row.home_team == "Oakland Athletics"
    assert row.home_moneyline_close == -140


def test_backfill_year_is_idempotent_replaces_same_year():
    with patch(
        "scripts.baseball.mlb_historical_odds_backfill.scraper.get_year_odds", return_value=_fake_odds(),
    ):
        backfill_year(2019)
        backfill_year(2019)
    assert MlbHistoricalOdds.select().count() == 1


def test_run_backfill_covers_full_range():
    with patch(
        "scripts.baseball.mlb_historical_odds_backfill.scraper.get_year_odds", return_value=[],
    ) as mock_get:
        results = run_backfill(2010, 2012)
    assert len(results) == 3
    assert mock_get.call_count == 3
