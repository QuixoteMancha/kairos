"""Tests de `migrate_players_to_canonical_entity.py` -- Fase 1, parte 2
de la migración a `Player` (2026-08-10, pedido explícito del usuario).
Migra retroactivamente las filas YA EXISTENTES en `MatchHistory` a la
entidad canónica, sin red."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player
from scripts.tennis.migrate_players_to_canonical_entity import (
    run_migration, _cluster_names_by_identity, _collect_best_info_by_exact_name,
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


def _row(match_id, player_a, player_b, **overrides):
    fields = dict(
        match_id=match_id, player_a_name=player_a, player_b_name=player_b,
        match_date="2026-08-11", source="tennisexplorer", fetched_at=_now(),
    )
    fields.update(overrides)
    return MatchHistory.create(**fields)


def test_cluster_names_by_identity_groups_format_variants():
    clusters = _cluster_names_by_identity(["Elina Svitolina", "Svitolina Elina", "Svitolina E."])
    assert len(clusters) == 1
    assert set(clusters[0]) == {"Elina Svitolina", "Svitolina Elina", "Svitolina E."}


def test_cluster_names_by_identity_keeps_same_surname_different_person_separate():
    clusters = _cluster_names_by_identity(["Casper Ruud", "Christian Ruud"])
    assert len(clusters) == 2


def test_collect_best_info_aggregates_across_rows():
    _row(
        "1", "Alexandrova Ekaterina", "Svitolina Elina", tour="WTA",
        player_a_slug="alexandrova-3181a", player_a_height_cm=180,
    )
    info = _collect_best_info_by_exact_name()
    assert info["Alexandrova Ekaterina"]["slug"] == "alexandrova-3181a"
    assert info["Alexandrova Ekaterina"]["height_cm"] == 180
    assert info["Alexandrova Ekaterina"]["tour"] == "WTA"


def test_run_migration_creates_one_player_per_real_person_across_name_formats():
    _row("1", "Elina Svitolina", "Alexandrova E.", tour="WTA")
    _row("2", "Ekaterina Alexandrova", "Svitolina E.", tour="WTA")

    result = run_migration()

    assert result["players_created"] == 2  # Svitolina + Alexandrova, no 4
    assert Player.select().count() == 2


def test_run_migration_backfills_fk_on_existing_rows():
    _row("1", "Elina Svitolina", "Alexandrova E.", tour="WTA")

    run_migration()

    row = MatchHistory.get(MatchHistory.match_id == "1")
    assert row.player_a_id is not None
    assert row.player_b_id is not None
    assert row.player_a.canonical_name in ("Elina Svitolina", "Svitolina Elina", "Svitolina E.")


def test_run_migration_never_overwrites_already_set_fk():
    _row("1", "Elina Svitolina", "Alexandrova E.", tour="WTA")
    manual_player = Player.create(canonical_name="Manually Set", created_at=_now())
    with db_historical.connection_context():
        MatchHistory.update(player_a_id=manual_player.id).where(MatchHistory.match_id == "1").execute()

    run_migration()

    row = MatchHistory.get(MatchHistory.match_id == "1")
    assert row.player_a_id == manual_player.id  # no se tocó, ya tenía FK


def test_run_migration_is_idempotent_on_second_run():
    _row("1", "Elina Svitolina", "Alexandrova E.", tour="WTA")
    run_migration()
    players_after_first = Player.select().count()

    result_second = run_migration()

    assert Player.select().count() == players_after_first
    assert result_second["rows_updated_player_a"] == 0  # ya tenía FK, nada que actualizar
    assert result_second["rows_updated_player_b"] == 0
