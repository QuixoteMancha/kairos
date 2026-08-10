"""Tests de `null_out_orphaned_player_fk.py` -- paso 1 de la reparación
del incidente real de `merge_duplicate_players.py` (2026-08-10, ver
docstring del módulo)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player
from scripts.tennis.null_out_orphaned_player_fk import (
    find_orphaned_fk_row_ids, run,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _row(match_id, player_a, player_b, player_a_id=None, player_b_id=None, **overrides):
    fields = dict(
        match_id=match_id, player_a_name=player_a, player_b_name=player_b,
        player_a_id=player_a_id, player_b_id=player_b_id,
        match_date="2026-08-11", source="tennisexplorer", fetched_at=_now(),
    )
    fields.update(overrides)
    return MatchHistory.create(**fields)


def test_finds_orphaned_player_a_id():
    real = Player.create(canonical_name="Real Player", created_at=_now())
    _row("1", "Some Name", "Real Player", player_a_id=99999, player_b_id=real.id)

    orphans = find_orphaned_fk_row_ids()

    assert len(orphans["player_a"]) == 1
    assert orphans["player_b"] == []


def test_finds_orphaned_player_b_id():
    real = Player.create(canonical_name="Real Player", created_at=_now())
    _row("1", "Real Player", "Some Name", player_a_id=real.id, player_b_id=88888)

    orphans = find_orphaned_fk_row_ids()

    assert orphans["player_a"] == []
    assert len(orphans["player_b"]) == 1


def test_leaves_valid_fk_untouched():
    real = Player.create(canonical_name="Real Player", created_at=_now())
    _row("1", "Real Player", "Real Player", player_a_id=real.id, player_b_id=real.id)

    orphans = find_orphaned_fk_row_ids()

    assert orphans == {"player_a": [], "player_b": []}


def test_apply_nulls_only_orphaned_side():
    real = Player.create(canonical_name="Real Player", created_at=_now())
    row = _row("1", "Some Name", "Real Player", player_a_id=99999, player_b_id=real.id)

    result = run(apply=True)

    assert result["player_a_nulled"] == 1
    assert result["player_b_nulled"] == 0
    reloaded = MatchHistory.get(MatchHistory.match_id == "1")
    assert reloaded.player_a_id is None
    assert reloaded.player_b_id == real.id  # intacto, no era huérfano


def test_dry_run_does_not_mutate():
    _row("1", "Some Name", "Other Name", player_a_id=99999, player_b_id=88888)

    result = run(apply=False)

    assert result["player_a_orphans_found"] == 1
    assert result["player_b_orphans_found"] == 1
    reloaded = MatchHistory.get(MatchHistory.match_id == "1")
    assert reloaded.player_a_id == 99999  # sin cambios
    assert reloaded.player_b_id == 88888
