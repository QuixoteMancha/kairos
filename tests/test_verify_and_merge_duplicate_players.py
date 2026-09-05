from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory, Player
from database.historical.tennis.models import (
    PlayerRankingHistory, PlayerStats, TennisAbstractElo, TennisPlayerRanking,
    TennisPlayerStrength,
)
from scripts.tennis import normalize_abbreviated_player_names as name_normalizer
from scripts.tennis.verify_and_merge_duplicate_players import (
    _merge, _reconcile_verified_group, _sample_match_id,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        for model in (
            MatchHistory, TennisPlayerStrength, TennisPlayerRanking,
            TennisAbstractElo, PlayerStats, PlayerRankingHistory, Player,
        ):
            model.delete().execute()
    yield
    with db_historical.connection_context():
        for model in (
            MatchHistory, TennisPlayerStrength, TennisPlayerRanking,
            TennisAbstractElo, PlayerStats, PlayerRankingHistory, Player,
        ):
            model.delete().execute()


def _player(name: str, slug: str = "shared") -> Player:
    return Player.create(
        canonical_name=name, slug=slug, created_at=datetime.now(timezone.utc),
    )


def _match(match_id: str, player_a: Player, player_b: Player) -> MatchHistory:
    return MatchHistory.create(
        match_id=match_id, source="tennisexplorer", player_a=player_a,
        player_b=player_b, fetched_at=datetime.now(timezone.utc),
    )


def test_merge_skips_orphan_model_tables_without_rolling_back(monkeypatch):
    major = _player("Major Player", "major-real")
    minor = _player("Minor Player", "minor-real")
    match = _match("1001", minor, major)
    PlayerStats.create(
        player_name=minor.canonical_name, surface="Hard", player=minor,
        fetched_at=datetime.now(timezone.utc),
    )

    real_table_exists = db_historical.table_exists
    absent_tables = {
        TennisAbstractElo._meta.table_name,
        PlayerRankingHistory._meta.table_name,
    }
    monkeypatch.setattr(
        db_historical,
        "table_exists",
        lambda table_name: False if table_name in absent_tables else real_table_exists(table_name),
    )

    _merge(minor, major, apply=True)

    assert Player.get_or_none(Player.id == minor.id) is None
    assert MatchHistory.get_by_id(match.id).player_a_id == major.id
    assert PlayerStats.get().player_id == major.id


def test_merge_moves_all_player_references_and_keeps_latest_conflicts():
    now = datetime.now(timezone.utc)
    major = _player("Major Player", "major-real")
    minor = _player("Minor Player", "minor-real")
    match = _match("1001", minor, major)
    match.winner = minor
    match.player_retired = minor
    match.save()

    TennisPlayerRanking.create(
        player_name=minor.canonical_name, tour="ATP", ranking=99,
        player=minor, fetched_at=now,
    )
    TennisAbstractElo.create(player=minor, elo=1510, fetched_at=now)
    PlayerStats.create(
        player_name=minor.canonical_name, surface="Hard", player=minor,
        fetched_at=now,
    )
    TennisPlayerStrength.create(
        player=major, tour="ATP", overall_elo=1500, updated_at=now,
    )
    TennisPlayerStrength.create(
        player=minor, tour="ATP", overall_elo=1700,
        updated_at=now + timedelta(minutes=1),
    )
    PlayerRankingHistory.create(
        player=major, ranked_at="2026-08-01", singles_ranking=80,
        fetched_at=now,
    )
    PlayerRankingHistory.create(
        player=minor, ranked_at="2026-08-01", singles_ranking=70,
        fetched_at=now + timedelta(minutes=1),
    )
    PlayerRankingHistory.create(
        player=minor, ranked_at="2026-08-08", singles_ranking=68,
        fetched_at=now,
    )

    _merge(minor, major, apply=True)

    assert Player.get_or_none(Player.id == minor.id) is None
    merged_match = MatchHistory.get_by_id(match.id)
    assert (merged_match.player_a_id, merged_match.player_b_id) == (major.id, major.id)
    assert merged_match.winner_id == major.id
    assert merged_match.player_retired_id == major.id
    assert TennisPlayerRanking.get().player_id == major.id
    assert TennisAbstractElo.get().player_id == major.id
    assert PlayerStats.get().player_id == major.id
    assert TennisPlayerStrength.select().count() == 1
    assert TennisPlayerStrength.get().overall_elo == 1700
    histories = list(PlayerRankingHistory.select().order_by(PlayerRankingHistory.ranked_at))
    assert [(row.ranked_at, row.singles_ranking, row.player_id) for row in histories] == [
        ("2026-08-01", 70, major.id), ("2026-08-08", 68, major.id),
    ]


def test_mixed_verified_group_merges_only_the_shared_identity():
    first = _player("First Format")
    second = _player("Second Format")
    distinct = _player("Different Person")
    _match("2001", first, distinct)
    _match("2002", second, distinct)
    _match("2003", distinct, first)

    merged, corrected = _reconcile_verified_group(
        [first, second, distinct],
        {first.id: "real-a", second.id: "real-a", distinct.id: "real-b"},
        apply=True,
    )

    assert merged == 1
    assert corrected == 2
    assert Player.select().count() == 2
    assert Player.get_by_id(first.id).slug == "real-a"
    assert Player.get_by_id(distinct.id).slug == "real-b"


def test_sample_match_id_ignores_synthetic_ids():
    player = _player("Verified Player")
    opponent = _player("Opponent")
    _match("kalshi:KXATPMATCH-1", player, opponent)
    _match("recentform:verified|opponent", player, opponent)
    _match("3001", player, opponent)

    assert _sample_match_id(player) == ("3001", "a")


def test_both_identity_scripts_only_use_numeric_tennisexplorer_ids():
    player = _player("Verified Player")
    opponent = _player("Opponent")
    MatchHistory.create(
        match_id="recentform:verified|opponent", source="recentform",
        player_a=player, player_b=opponent, fetched_at=datetime.now(timezone.utc),
    )
    MatchHistory.create(
        match_id="not-a-match-detail-id", source="tennisexplorer",
        player_a=player, player_b=opponent, fetched_at=datetime.now(timezone.utc),
    )
    _match("3001", player, opponent)

    assert _sample_match_id(player) == ("3001", "a")
    assert name_normalizer._sample_match_id(player) == ("3001", "a")


def test_name_normalizer_fails_closed_when_live_lookup_raises(monkeypatch):
    player = _player("Player V.")
    opponent = _player("Opponent")
    _match("3001", player, opponent)

    def fail_lookup(_match_id):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(name_normalizer, "get_match_detail", fail_lookup)

    assert name_normalizer._verify_full_name(player) is None
