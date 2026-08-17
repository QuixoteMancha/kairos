from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis import (
    MatchHistory, Player, TennisEloCalibration, TennisPlayerStrength,
)
from scripts.tennis.compute_weighted_elo import run_weighted_elo_backfill


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    with db_historical.connection_context():
        TennisEloCalibration.delete().execute()
        TennisPlayerStrength.delete().execute()
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        TennisEloCalibration.delete().execute()
        TennisPlayerStrength.delete().execute()
        MatchHistory.delete().execute()
        Player.delete().execute()


def test_backfill_writes_point_in_time_surface_ratings_and_current_strength():
    now = datetime.now(timezone.utc)
    a = Player.create(canonical_name="A", tour="ATP", created_at=now)
    b = Player.create(canonical_name="B", tour="ATP", created_at=now)
    MatchHistory.create(
        match_id="w1", tour="ATP", match_date="2024-01-01", surface="Hard",
        score="6-4, 6-3", player_a=a, player_b=b, winner=a, fetched_at=now,
    )
    MatchHistory.create(
        match_id="w2", tour="ATP", match_date="2024-02-01", surface="Hard",
        score="4-6, 6-3, 6-4", player_a=a, player_b=b, winner=b, fetched_at=now,
    )

    result = run_weighted_elo_backfill(tours=("ATP",))

    first = MatchHistory.get(MatchHistory.match_id == "w1")
    second = MatchHistory.get(MatchHistory.match_id == "w2")
    assert first.player_a_weighted_elo == 1500.0
    assert first.player_a_surface_elo == 1500.0
    assert second.player_a_weighted_elo > second.player_b_weighted_elo
    assert TennisPlayerStrength.select().where(TennisPlayerStrength.tour == "ATP").count() == 2
    calibration = TennisEloCalibration.get()
    assert calibration.surface == "Hard"
    assert calibration.sample_count == 4
    assert result["ATP"]["processed"] == 2
