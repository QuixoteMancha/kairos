"""Tests de la arquitectura DB-first de TML-Database (2026-08-04,
pedido explícito del usuario: "el backtesting se debe hacer con datos
de base, nada de estar llamando a fuentes en red a cada rato")."""

from __future__ import annotations

import logging
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis import TmlDatabaseMatch
from outer_apis.scrapers.tennis.tml_database_scraper import (
    save_matches_to_db, has_cached_year, get_matches_range_from_db,
)
from scripts.tml_database_local_backfill import run_backfill


def _match(date="2020-01-01", winner="A", loser="B", level="M", **kw):
    return {
        "tourney_name": "X", "level": level, "date": date, "round": "F",
        "surface": "Hard", "best_of": 3,
        "winner_name": winner, "winner_rank": 5, "winner_age": 25.0,
        "loser_name": loser, "loser_rank": 50, "loser_age": 25.0,
        "score": "6-4 6-3", "minutes": 95,
        **kw,
    }


def setup_function(_fn):
    init_db_historical()
    TmlDatabaseMatch.delete().execute()


def test_has_cached_year_false_when_empty():
    assert has_cached_year(2099) is False


def test_save_and_has_cached_year():
    save_matches_to_db(2099, [_match()])
    assert has_cached_year(2099) is True


def test_save_matches_to_db_is_idempotent_replaces_year():
    save_matches_to_db(2099, [_match(winner="A"), _match(winner="C")])
    assert TmlDatabaseMatch.select().where(TmlDatabaseMatch.year == 2099).count() == 2
    save_matches_to_db(2099, [_match(winner="Z")])  # re-backfill del mismo año
    rows = list(TmlDatabaseMatch.select().where(TmlDatabaseMatch.year == 2099))
    assert len(rows) == 1
    assert rows[0].winner_name == "Z"


def test_get_matches_range_from_db_never_touches_network():
    save_matches_to_db(2099, [_match(level="M")])
    with patch("outer_apis.scrapers.tennis.tml_database_scraper.requests.get") as mock_get:
        result = get_matches_range_from_db(2099, 2099)
        mock_get.assert_not_called()
    assert len(result) == 1
    assert result[0]["winner_name"] == "A"


def test_get_matches_range_from_db_filters_by_level():
    save_matches_to_db(2099, [_match(level="M"), _match(level="D", winner="Z")])
    result = get_matches_range_from_db(2099, 2099)  # default TOUR_LEVELS_250_PLUS excluye 'D'
    assert len(result) == 1
    assert result[0]["winner_name"] == "A"


def test_get_matches_range_from_db_warns_on_missing_year(caplog):
    with caplog.at_level(logging.WARNING):
        result = get_matches_range_from_db(2098, 2098)
    assert result == []
    assert any("backfill" in r.message for r in caplog.records)


def test_run_backfill_persists_and_reports_counts():
    fake_matches = [_match(date="2099-01-01")]
    with patch(
        "scripts.tml_database_local_backfill.get_year_matches", return_value=fake_matches,
    ), patch("scripts.tml_database_local_backfill.time.sleep"):
        result = run_backfill(2099, 2099, force=True)
    assert result["years_ok"] == 1
    assert result["total_matches_saved"] == 1
    assert has_cached_year(2099) is True


def test_run_backfill_skips_already_cached_years_unless_forced():
    save_matches_to_db(2099, [_match()])
    with patch("scripts.tml_database_local_backfill.get_year_matches") as mock_fetch, \
         patch("scripts.tml_database_local_backfill.time.sleep"), \
         patch("scripts.tml_database_local_backfill.date") as mock_date:
        mock_date.today.return_value.year = 2030  # 2099 no es "el año en curso" en este test
        result = run_backfill(2099, 2099, force=False)
    mock_fetch.assert_not_called()
    assert result["years_skipped_cached"] == 1
