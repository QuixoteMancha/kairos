"""Tests de `save_match_duration`/`has_match_duration` (writer.py) --
2026-08-12, pedido explícito del usuario ("añadir un modelo para esto
MatchDuration y campo a la tabla de matches con foreignkey")."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, MatchDuration, Player
from database.historical.tennis.writer import save_match_duration, has_match_duration


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        MatchDuration.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        MatchDuration.delete().execute()
        Player.delete().execute()


def _make_match():
    a = Player.create(canonical_name="Player A", created_at=datetime.now(timezone.utc))
    b = Player.create(canonical_name="Player B", created_at=datetime.now(timezone.utc))
    return MatchHistory.create(
        match_id="test:1", source="tennisexplorer", match_date="2025-01-01",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
    )


_DURATION = {
    "total_minutes": 175, "set_1_minutes": 59, "set_2_minutes": 53, "set_3_minutes": 63,
    "set_4_minutes": None, "set_5_minutes": None,
}


def test_saves_and_connects_duration():
    match = _make_match()
    assert has_match_duration(match.id) is False

    result = save_match_duration(match.id, "fs123", _DURATION)

    assert result is True
    assert has_match_duration(match.id) is True
    row = MatchHistory.get(MatchHistory.id == match.id)
    assert row.duration.total_minutes == 175
    assert row.duration.set_3_minutes == 63
    assert row.duration.flashscore_match_id == "fs123"


def test_does_not_overwrite_existing_duration():
    match = _make_match()
    save_match_duration(match.id, "fs123", _DURATION)

    result = save_match_duration(match.id, "fs999", {"total_minutes": 100})

    assert result is False
    row = MatchHistory.get(MatchHistory.id == match.id)
    assert row.duration.flashscore_match_id == "fs123"  # sin pisar


def test_reuses_existing_matchduration_row_by_flashscore_id():
    # 2 partidos DISTINTOS de MatchHistory que por alguna razón
    # apuntaran al mismo flashscore_match_id (caso raro, pero el
    # mecanismo debe reusar, no duplicar).
    match1 = _make_match()
    a2 = Player.create(canonical_name="Player C", created_at=datetime.now(timezone.utc))
    b2 = Player.create(canonical_name="Player D", created_at=datetime.now(timezone.utc))
    match2 = MatchHistory.create(
        match_id="test:2", source="tennisexplorer", match_date="2025-01-02",
        player_a=a2, player_b=b2, fetched_at=datetime.now(timezone.utc),
    )

    save_match_duration(match1.id, "fs_shared", _DURATION)
    save_match_duration(match2.id, "fs_shared", _DURATION)

    assert MatchDuration.select().where(MatchDuration.flashscore_match_id == "fs_shared").count() == 1
    row1 = MatchHistory.get(MatchHistory.id == match1.id)
    row2 = MatchHistory.get(MatchHistory.id == match2.id)
    assert row1.duration_id == row2.duration_id


def test_fails_closed_on_empty_duration_data():
    match = _make_match()
    assert save_match_duration(match.id, "fs123", {}) is False
    assert has_match_duration(match.id) is False


def test_fails_closed_on_nonexistent_match():
    assert save_match_duration(999999, "fs123", _DURATION) is False


def test_has_match_duration_false_for_nonexistent_match():
    assert has_match_duration(999999) is False
