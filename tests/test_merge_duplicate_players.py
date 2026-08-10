"""Tests de `merge_duplicate_players.py` (2026-08-10, pedido explícito
del usuario tras encontrar 143 pares reales de `Player` duplicados en
producción -- misma persona, 2 filas en orden de nombre opuesto)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player
from scripts.tennis.merge_duplicate_players import find_duplicate_pairs, run


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


def test_finds_duplicate_pair_te_native_vs_correct():
    dup = Player.create(canonical_name="Knapp Karin", created_at=_now())
    survivor = Player.create(canonical_name="Karin Knapp", created_at=_now())
    _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")

    pairs = find_duplicate_pairs()

    assert len(pairs) == 1
    assert pairs[0][0].id == dup.id
    assert pairs[0][1].id == survivor.id


def test_apply_repoints_match_history_fks_to_survivor():
    dup = Player.create(canonical_name="Knapp Karin", created_at=_now())
    survivor = Player.create(canonical_name="Karin Knapp", created_at=_now())
    m1 = _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")
    m2 = _row("2", "Knapp Karin", "Opponent Y", player_a_id=dup.id, source="tennisexplorer")

    result = run(apply=True)

    assert len(result["merged"]) == 1
    m1_reloaded = MatchHistory.get(MatchHistory.match_id == "1")
    m2_reloaded = MatchHistory.get(MatchHistory.match_id == "2")
    assert m1_reloaded.player_b_id == survivor.id
    assert m2_reloaded.player_a_id == survivor.id


def test_apply_deletes_dup_player_row():
    dup = Player.create(canonical_name="Knapp Karin", created_at=_now())
    Player.create(canonical_name="Karin Knapp", created_at=_now())
    _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")

    run(apply=True)

    assert Player.get_or_none(Player.id == dup.id) is None


def test_apply_enriches_survivor_from_dup_null_fields():
    dup = Player.create(canonical_name="Knapp Karin", slug="knapp", tour="WTA", created_at=_now())
    survivor = Player.create(canonical_name="Karin Knapp", slug=None, tour=None, created_at=_now())
    _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")

    run(apply=True)

    reloaded = Player.get_by_id(survivor.id)
    assert reloaded.slug == "knapp"
    assert reloaded.tour == "WTA"


def test_apply_never_overwrites_survivor_confirmed_field():
    dup = Player.create(canonical_name="Knapp Karin", tour="ATP", created_at=_now())
    survivor = Player.create(canonical_name="Karin Knapp", tour="WTA", created_at=_now())
    _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")

    result = run(apply=True)

    # conflicto real (ATP vs WTA) -- debe saltarse, no fusionar a ciegas
    assert result["merged"] == []
    assert len(result["conflicts_skipped"]) == 1
    assert Player.get_or_none(Player.id == dup.id) is not None
    reloaded = Player.get_by_id(survivor.id)
    assert reloaded.tour == "WTA"


def test_dry_run_does_not_mutate():
    dup = Player.create(canonical_name="Knapp Karin", created_at=_now())
    survivor = Player.create(canonical_name="Karin Knapp", created_at=_now())
    _row("1", "Opponent X", "Knapp Karin", player_b_id=dup.id, source="tennisexplorer")

    result = run(apply=False)

    assert len(result["merged"]) == 1
    assert Player.get_or_none(Player.id == dup.id) is not None
    m1_reloaded = MatchHistory.get(MatchHistory.match_id == "1")
    assert m1_reloaded.player_b_id == dup.id
