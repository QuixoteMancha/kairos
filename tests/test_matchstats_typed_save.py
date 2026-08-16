"""Test end-to-end del guardado de estadísticas -- 2026-08-16.

Regresión del bug real: `save_match_stats_for_history` escribía SOLO
`raw_stats_json`; las ~40 columnas tipadas quedaban NULL (42/42 filas en
producción) y `MatchStatsSet` nunca recibía filas pese a que el feed trae
`set_1`/`set_2`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory
from database.historical.tennis.matchstats import save_match_stats_for_history
from database.historical.tennis.models import MatchStats, MatchStatsSet


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchStatsSet.delete().execute()
        MatchStats.delete().execute()
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        MatchStatsSet.delete().execute()
        MatchStats.delete().execute()
        MatchHistory.delete().execute()


_FEED = {
    "match": {
        "Aces": ["2", "8"],
        "1st serve percentage": ["75%", "63%"],
        "Break Points Saved": ["4/7", "0/1"],
        "Service games won": ["70% (7/10)", "90% (9/10)"],
    },
    "set_1": {"Aces": ["1", "3"], "1st serve percentage": ["70%", "60%"]},
    "set_2": {"Aces": ["1", "5"], "1st serve percentage": ["80%", "66%"]},
}


def _match() -> MatchHistory:
    return MatchHistory.create(
        match_id="m1", match_date="2025-01-01", fetched_at=datetime.now(timezone.utc),
    )


def test_save_populates_typed_columns_not_just_json():
    mh = _match()
    assert save_match_stats_for_history(mh.id, "fsid", _FEED) is True

    row = MatchStats.get(MatchStats.match_history == mh.id)
    assert row.raw_stats_json  # el JSON crudo se sigue guardando
    # ...y ahora TAMBIÉN las columnas tipadas (esto era lo roto)
    assert row.aces_a == 2 and row.aces_b == 8
    assert row.first_serve_pct_a == 75.0
    assert row.break_points_saved_a == 4 and row.break_points_faced_a == 7
    assert row.service_games_won_a == 7 and row.serve_games_played_a == 10


def test_save_creates_one_row_per_set():
    mh = _match()
    save_match_stats_for_history(mh.id, "fsid", _FEED)

    sets = list(
        MatchStatsSet.select()
        .where(MatchStatsSet.match_history == mh.id)
        .order_by(MatchStatsSet.set_number)
    )
    assert [s.set_number for s in sets] == [1, 2]
    assert sets[0].aces_a == 1 and sets[0].aces_b == 3
    assert sets[1].first_serve_pct_a == 80.0


def test_save_is_still_idempotent():
    mh = _match()
    assert save_match_stats_for_history(mh.id, "fsid", _FEED) is True
    assert save_match_stats_for_history(mh.id, "fsid", _FEED) is False
    assert MatchStats.select().where(MatchStats.match_history == mh.id).count() == 1


def test_feed_without_sets_still_saves_the_match_row():
    mh = _match()
    assert save_match_stats_for_history(mh.id, "fsid", {"match": {"Aces": ["1", "2"]}}) is True
    assert MatchStats.select().count() == 1
    assert MatchStatsSet.select().count() == 0
