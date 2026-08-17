from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis import TennisPlayerRanking
from database.historical.tennis.writer import save_rankings_snapshot


@pytest.fixture(autouse=True)
def clean_rankings():
    init_db_historical()
    TennisPlayerRanking.delete().execute()
    yield
    TennisPlayerRanking.delete().execute()


def test_only_one_ranking_snapshot_per_tour_per_day():
    players = [
        {"nombre": "Player One", "ranking": 1, "pais": "CL"},
        {"nombre": "Player Two", "ranking": 2, "pais": "AR"},
    ]

    save_rankings_snapshot("ATP", players)
    save_rankings_snapshot("ATP", players)

    assert TennisPlayerRanking.select().count() == 2
    fetched_days = {
        row.fetched_at.replace(tzinfo=timezone.utc).date()
        if row.fetched_at.tzinfo is None else row.fetched_at.date()
        for row in TennisPlayerRanking.select()
    }
    assert fetched_days == {datetime.now(timezone.utc).date()}


def test_tours_are_deduplicated_independently():
    player = [{"nombre": "Player One", "ranking": 1, "pais": "CL"}]
    save_rankings_snapshot("ATP", player)
    save_rankings_snapshot("WTA", player)
    assert TennisPlayerRanking.select().count() == 2
