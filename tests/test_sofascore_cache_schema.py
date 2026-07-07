"""
test_sofascore_cache_schema.py — regression test for the schema-drift bug in
src/scrapers/player_stats/sofascore.py::_sofascore_sync_matches.

Before the fix, this function called SofascoreMatchCache.create() with field
names (`fecha`, `is_home`, `winner_is_player`, `category_name`) that no
longer exist on the current model (domain.py defines `date`, `tournament`,
`p1_name`/`p2_name`/`winner_name` instead) — every insert would have raised.
This never surfaced because the live Sofascore client returns None for every
request (403 challenge), so the code path was simply never exercised.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


class _FakeScraper:
    def __init__(self, matches):
        self._matches = matches

    def get_player_last_matches(self, pid, n=20, categories=None):
        return self._matches


def test_sync_matches_creates_rows_with_current_schema():
    from src.scrapers.player_stats.sofascore import _sofascore_sync_matches
    from src.database.database import SofascoreMatchCache

    fake_matches = [
        {
            "sofascore_id": 555,
            "home_id": 42,
            "away_id": 99,
            "resultado": "2-0",
            "fecha": "2024-03-01",
            "surface": "Clay",
            "period_scores": "6,3;6,4",
            "category_name": "ATP",
        }
    ]
    sc = _FakeScraper(fake_matches)

    rows = _sofascore_sync_matches("Test Player", pid=42, sc=sc, target=5)

    assert len(rows) == 1
    row = SofascoreMatchCache.get(SofascoreMatchCache.event_id == 555)
    assert row.player_name == "Test Player"
    assert row.date == "2024-03-01"
    assert row.tournament == "ATP"
    assert row.surface == "Clay"
    assert row.p1_name == "Test Player"
    assert row.winner_name == "Test Player"  # home_id == pid and player won


def test_sync_bp_stats_marks_rows_fetched():
    from src.scrapers.player_stats.sofascore import (
        _sofascore_sync_matches,
        _sofascore_sync_bp_stats,
    )

    fake_matches = [
        {
            "sofascore_id": 556,
            "home_id": 42,
            "away_id": 99,
            "resultado": "0-2",
            "fecha": "2024-03-02",
            "surface": "Hard",
            "period_scores": "3,6;2,6",
            "category_name": "ATP",
        }
    ]
    sc = _FakeScraper(fake_matches)
    rows = _sofascore_sync_matches("Test Player", pid=42, sc=sc, target=5)
    assert rows[0].bp_stats_fetched is False

    _sofascore_sync_bp_stats(rows, sc)
    rows[0].refresh = None  # peewee objects don't auto-refresh; re-fetch below

    from src.database.database import SofascoreMatchCache
    refreshed = SofascoreMatchCache.get(SofascoreMatchCache.event_id == 556)
    assert refreshed.bp_stats_fetched is True
