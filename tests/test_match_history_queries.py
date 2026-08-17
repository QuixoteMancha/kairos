from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory, Player, Tournament
from database.historical.tennis.queries import get_head_to_head, get_player_matches, get_surface_form


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    MatchHistory.delete().execute()
    Player.delete().execute()
    Tournament.delete().execute()
    yield
    MatchHistory.delete().execute()
    Player.delete().execute()
    Tournament.delete().execute()


def test_recent_surface_and_h2h_are_derived_from_canonical_matches():
    now = datetime.now(timezone.utc)
    a = Player.create(canonical_name="Player A", created_at=now)
    b = Player.create(canonical_name="Player B", created_at=now)
    tournament = Tournament.create(year=2026, tournament_name="Cincinnati", city="Cincinnati", fetched_at=now)
    MatchHistory.create(match_id="real-1", match_date="2026-08-13", surface="Hard",
                        player_a=a, player_b=b, winner=a, tournament=tournament, fetched_at=now)

    rows = get_player_matches("Player A")
    assert rows[0]["opponent"] == "Player B"
    assert rows[0]["tournament"] == "Cincinnati"
    assert get_surface_form("Player A", "Hard", min_matches=1)["win_rate"] == 1.0
    assert get_head_to_head("Player A", "Player B") == {"a_wins": 1, "b_wins": 0, "n_matches": 1}


def test_recentform_rows_are_never_returned():
    now = datetime.now(timezone.utc)
    a = Player.create(canonical_name="Player A", created_at=now)
    b = Player.create(canonical_name="Player B", created_at=now)
    MatchHistory.create(match_id="recentform:old", match_date="2026-08-13",
                        player_a=a, player_b=b, winner=a, fetched_at=now)
    assert get_player_matches("Player A") == []
