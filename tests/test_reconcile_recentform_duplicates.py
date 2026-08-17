from datetime import datetime, timezone

from database.historical.tennis import MatchHistory, Player
from scripts.tennis.reconcile_recentform_duplicates import (
    reconcile_recentform_duplicates,
)


def _player(name):
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def test_reconciles_only_unique_confirmed_recentform_duplicate():
    zhang = _player("Shuai Zhang")
    abbreviated = _player("Day K.")
    canonical = _player("Kayla Day")
    MatchHistory.create(
        match_id="recentform:day k.|shuai zhang|2026-08-15",
        player_a=zhang, player_b=abbreviated, match_date="2026-08-15",
        source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
    )
    MatchHistory.create(
        match_id="3294705", player_a=zhang, player_b=canonical,
        match_date="2026-08-15", source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )

    dry_run = reconcile_recentform_duplicates()
    assert dry_run["matched"] == 1
    assert dry_run["deleted"] == 0
    assert MatchHistory.select().count() == 2

    applied = reconcile_recentform_duplicates(apply=True)
    assert applied["deleted"] == 1
    assert MatchHistory.select().count() == 1
    assert MatchHistory.get().match_id == "3294705"
