"""
test_tennis_history_fallback.py — DB -> Sofascore -> Flashscore fallback for
player match HISTORY (`tennis_data.populate_player_history`).

Before this, `RecentMatchHistory` (the table `compute.py` reads for
form/win%) had no real writer anywhere in the live pipeline: `_sofascore_
sync_matches` and Flashscore's `find_match_result` existed but nothing
chained "try Sofascore, if empty try Flashscore" and persisted the result.
This test confirms the chain with mocked HTTP (no real network), covering:
  1. DB already has enough rows -> no network call at all.
  2. DB empty, Sofascore returns real matches -> persisted, source=sofascore.
  3. DB empty, Sofascore blocked/empty -> Flashscore fallback kicks in and
     persists, source=flashscore.
  4. Both sources empty -> source=none, nothing persisted.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


@pytest.fixture
def _player(_db):
    from src.database.database import Player
    p, _ = Player.get_or_create(nombre="Jannik Sinner", defaults={"ranking_type": "ATP"})
    yield p
    p.delete_instance(recursive=True)


def test_db_already_populated_skips_network(monkeypatch, _player):
    from src.database.database import RecentMatchHistory
    from datetime import date, timedelta

    for i in range(5):
        RecentMatchHistory.create(
            player=_player, fecha=date.today() - timedelta(days=i),
            torneo="X", oponente=f"Rival {i}", score="6-4 6-3",
            superficie="Hard", resultado="W", minutos=90, ubicacion_torneo="",
        )

    def _boom(*a, **kw):
        raise AssertionError("should not hit Sofascore when DB already has enough rows")

    monkeypatch.setattr(
        "src.scrapers.sofascore_scraper.SofascoreScraper.search_player_id", _boom,
    )

    from src.core.orchestration.tennis_data import populate_player_history
    res = populate_player_history(_player, "Jannik Sinner", target=5)
    assert res == {"source": "db", "new_rows": 0}


def test_sofascore_success_populates_history(monkeypatch, _player):
    class _FakeSofascoreCacheRow:
        def __init__(self, date_, tournament, surface, winner_name, period_scores):
            self.date = date_
            self.tournament = tournament
            self.surface = surface
            self.winner_name = winner_name
            self.period_scores = period_scores

    fake_rows = [
        _FakeSofascoreCacheRow("2026-07-01", "Wimbledon", "Grass", "Jannik Sinner", "6,4;6,3"),
        _FakeSofascoreCacheRow("2026-06-20", "Halle", "Grass", "Some Rival", "3,6;4,6"),
    ]

    monkeypatch.setattr(
        "src.scrapers.sofascore_scraper.SofascoreScraper.search_player_id",
        lambda self, name: 12345,
    )
    monkeypatch.setattr(
        "src.scrapers.player_stats.sofascore._sofascore_sync_matches",
        lambda player_name, pid, sc, target=20: fake_rows,
    )

    def _fs_boom(*a, **kw):
        raise AssertionError("should not fall back to Flashscore when Sofascore succeeded")

    monkeypatch.setattr(
        "src.scrapers.flashscore_scraper.get_player_recent_matches_fs", _fs_boom,
    )

    from src.core.orchestration.tennis_data import populate_player_history
    from src.database.database import RecentMatchHistory

    res = populate_player_history(_player, "Jannik Sinner", target=20)
    assert res["source"] == "sofascore"
    assert res["new_rows"] == 2

    rows = list(RecentMatchHistory.select().where(RecentMatchHistory.player == _player))
    assert len(rows) == 2
    resultados = sorted(r.resultado for r in rows)
    assert resultados == ["L", "W"]


def test_sofascore_empty_falls_back_to_flashscore(monkeypatch, _player):
    monkeypatch.setattr(
        "src.scrapers.sofascore_scraper.SofascoreScraper.search_player_id",
        lambda self, name: None,  # simulates Sofascore blocked/not-found
    )

    fake_fs_rows = [
        {
            "fecha": "2026-07-01", "oponente": "Carlos Alcaraz",
            "resultado": "W", "score": "6-4 6-3",
            "superficie": "", "torneo": "", "source": "flashscore",
        },
    ]
    monkeypatch.setattr(
        "src.scrapers.flashscore_scraper.get_player_recent_matches_fs",
        lambda name, n=20: fake_fs_rows,
    )

    from src.core.orchestration.tennis_data import populate_player_history
    from src.database.database import RecentMatchHistory

    res = populate_player_history(_player, "Jannik Sinner", target=20)
    assert res == {"source": "flashscore", "new_rows": 1}

    row = RecentMatchHistory.get(RecentMatchHistory.player == _player)
    assert row.oponente == "Carlos Alcaraz"
    assert row.resultado == "W"


def test_both_sources_empty_returns_none(monkeypatch, _player):
    monkeypatch.setattr(
        "src.scrapers.sofascore_scraper.SofascoreScraper.search_player_id",
        lambda self, name: None,
    )
    monkeypatch.setattr(
        "src.scrapers.flashscore_scraper.get_player_recent_matches_fs",
        lambda name, n=20: [],
    )

    from src.core.orchestration.tennis_data import populate_player_history
    from src.database.database import RecentMatchHistory

    res = populate_player_history(_player, "Jannik Sinner", target=20)
    assert res == {"source": "none", "new_rows": 0}
    assert RecentMatchHistory.select().where(RecentMatchHistory.player == _player).count() == 0
