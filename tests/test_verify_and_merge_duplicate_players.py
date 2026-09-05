from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory, Player
from database.historical.tennis.models import (
    PlayerRankingHistory, PlayerStats, TennisAbstractElo, TennisPlayerRanking,
    TennisPlayerStrength, Tournament,
)
from scripts.tennis import normalize_abbreviated_player_names as name_normalizer
from scripts.tennis import tennisexplorer_identity as identity_helpers
from scripts.tennis.verify_and_merge_duplicate_players import (
    _merge, _reconcile_verified_group, _sample_match_id,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        for model in (
            MatchHistory, TennisPlayerStrength, TennisPlayerRanking,
            TennisAbstractElo, PlayerStats, PlayerRankingHistory, Tournament, Player,
        ):
            model.delete().execute()
    yield
    with db_historical.connection_context():
        for model in (
            MatchHistory, TennisPlayerStrength, TennisPlayerRanking,
            TennisAbstractElo, PlayerStats, PlayerRankingHistory, Tournament, Player,
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


def test_identity_fallback_uses_only_a_validated_verified_rivals_next_match(monkeypatch):
    unknown = _player("Cirstea S.", "cirstea")
    rival = _player("Jessica Pegula", "pegula")
    other = _player("Other Rival", "other")
    tournament = Tournament.create(
        year=2026, tournament_name="US Open WTA", city="",
        fetched_at=datetime.now(timezone.utc),
    )
    # The rival already has a direct TE row, making their profile a valid
    # bridge. The unresolved player has only the synthetic Kalshi row.
    _match("8001", rival, other)
    MatchHistory.create(
        match_id="kalshi:KXWTAMATCH-26SEP06PEGCIR", source="kalshi",
        match_date="2026-09-06", tournament=tournament,
        player_a=rival, player_b=unknown, fetched_at=datetime.now(timezone.utc),
    )
    next_match = {
        "match_id": "9001",
        "tournament": "US Open Women",
        "start_raw": "06.09. 12:00",
        "match_text": "Pegula J. - Cirstea S.",
    }
    monkeypatch.setattr(identity_helpers, "get_next_match", lambda name: next_match)
    detail = {
        "player_a_name": "Jessica Pegula",
        "player_a_slug": "pegula",
        "player_b_name": "Sorana-Mihaela Cirstea",
        "player_b_slug": "cirstea",
    }
    monkeypatch.setattr(name_normalizer, "get_match_detail", lambda match_id: detail)

    assert identity_helpers.sample_tennisexplorer_match_id(unknown) == ("9001", None)
    assert name_normalizer._verify_full_name(unknown) == "Sorana-Mihaela Cirstea"


def test_identity_fallback_recovers_historical_kalshi_row_from_one_real_match():
    unknown = _player("Cirstea S.", "cirstea")
    rival = _player("Jessica Pegula", "pegula")
    resolved_identity = _player("Sorana-Mihaela Cirstea", "cirstea")
    other = _player("Other Rival", "other")
    tournament = Tournament.create(
        year=2026, tournament_name="Cincinnati", city="",
        fetched_at=datetime.now(timezone.utc),
    )
    _match("8001", rival, other)  # proves the rival has a TE identity
    MatchHistory.create(
        match_id="kalshi:KXWTAMATCH-26AUG19CIRPEG", source="kalshi",
        match_date="2026-08-19", tournament=tournament,
        player_a=rival, player_b=unknown, fetched_at=datetime.now(timezone.utc),
    )
    MatchHistory.create(
        match_id="9001", source="tennisexplorer", match_date="2026-08-19",
        tournament=tournament, player_a=rival, player_b=resolved_identity,
        fetched_at=datetime.now(timezone.utc),
    )

    assert identity_helpers.sample_tennisexplorer_match_id(unknown) == ("9001", None)


def test_identity_fallback_rejects_next_match_without_exact_date_tournament_and_pair(monkeypatch):
    unknown = _player("Cirstea S.", "cirstea")
    rival = _player("Jessica Pegula", "pegula")
    other = _player("Other Rival", "other")
    tournament = Tournament.create(
        year=2026, tournament_name="US Open WTA", city="",
        fetched_at=datetime.now(timezone.utc),
    )
    _match("8001", rival, other)
    MatchHistory.create(
        match_id="kalshi:KXWTAMATCH-26SEP06PEGCIR", source="kalshi",
        match_date="2026-09-06", tournament=tournament,
        player_a=rival, player_b=unknown, fetched_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(identity_helpers, "get_next_match", lambda name: {
        "match_id": "9001",
        "tournament": "Guadalajara WTA",
        "start_raw": "07.09. 12:00",
        "match_text": "Pegula J. - Someone E.",
    })

    assert identity_helpers.sample_tennisexplorer_match_id(unknown) is None
