"""Tests de `save_match_timing`/`has_match_timing` (writer.py) --
2026-09-05, pedido explícito del usuario ("engancharlo a worker y
hacerlo persistir en base") sobre `flashscore_scraper.get_match_timing()`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, MatchDuration, Player
from database.historical.tennis.writer import save_match_timing, has_match_timing


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


_TIMING = {
    "start_at": "2026-09-05T17:10:00+00:00",
    "end_at": "2026-09-05T19:53:57+00:00",
    "duration_minutes": 164,
}


def test_saves_and_connects_timing():
    match = _make_match()
    assert has_match_timing(match.id) is False

    result = save_match_timing(match.id, "fs123", _TIMING)

    assert result is True
    assert has_match_timing(match.id) is True
    row = MatchHistory.get(MatchHistory.id == match.id)
    assert row.duration.flashscore_match_id == "fs123"
    assert str(row.duration.start_at) == "2026-09-05 17:10:00+00:00"
    assert str(row.duration.end_at) == "2026-09-05 19:53:57+00:00"


def test_does_not_overwrite_existing_timing():
    match = _make_match()
    save_match_timing(match.id, "fs123", _TIMING)

    other = {"start_at": "2020-01-01T00:00:00+00:00", "end_at": "2020-01-01T02:00:00+00:00"}
    save_match_timing(match.id, "fs123", other)

    row = MatchHistory.get(MatchHistory.id == match.id)
    assert str(row.duration.start_at) == "2026-09-05 17:10:00+00:00"


def test_reuses_existing_matchduration_row_created_by_duration_pipeline():
    # `MatchDuration` ya puede existir (creada por `save_match_duration`
    # vía el pipeline de enriquecimiento) -- el timing debe completarla,
    # no crear una fila duplicada.
    from database.historical.tennis.writer import save_match_duration

    match = _make_match()
    save_match_duration(match.id, "fs123", {"total_minutes": 165})

    result = save_match_timing(match.id, "fs123", _TIMING)

    assert result is True
    assert MatchDuration.select().where(MatchDuration.flashscore_match_id == "fs123").count() == 1
    row = MatchHistory.get(MatchHistory.id == match.id)
    assert row.duration.total_minutes == 165
    assert row.duration.start_at is not None


def test_fails_closed_on_empty_timing_data():
    match = _make_match()
    assert save_match_timing(match.id, "fs123", {}) is False
    assert has_match_timing(match.id) is False


def test_fails_closed_on_nonexistent_match():
    assert save_match_timing(999999, "fs123", _TIMING) is False


def test_has_match_timing_false_for_nonexistent_match():
    assert has_match_timing(999999) is False


def test_has_match_timing_false_when_duration_exists_without_start_at():
    from database.historical.tennis.writer import save_match_duration

    match = _make_match()
    save_match_duration(match.id, "fs123", {"total_minutes": 165})

    assert has_match_timing(match.id) is False
