"""Tests de la migración completa TML -> `MatchHistory` -- 2026-08-08,
pedido explícito del usuario: "migraremos todo lo de TML a una única
base que se llamará matchhistory"."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TmlDatabaseMatch, MatchHistory
from scripts.tennis.migrate_tml_to_match_history import migrate_all_tml_rows


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TmlDatabaseMatch.delete().execute()
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        TmlDatabaseMatch.delete().execute()
        MatchHistory.delete().execute()


def _tml_create(winner, loser, year=2024, date="2024-08-01", **kw):
    defaults = {
        "year": year, "date": date, "winner_name": winner, "loser_name": loser,
        "tourney_name": "Toronto", "round": "F", "surface": "hard", "score": "6-4 6-2",
        "winner_rank": 5, "loser_rank": 10,
    }
    defaults.update(kw)
    return TmlDatabaseMatch.create(**defaults)


def test_migrates_every_row_on_first_run():
    _tml_create("Player A", "Player B")
    _tml_create("Player C", "Player D")
    summary = migrate_all_tml_rows()
    assert summary["created"] == 2
    assert MatchHistory.select().count() == 2


def test_does_not_duplicate_on_second_run_with_identical_data():
    _tml_create("Player A", "Player B")
    migrate_all_tml_rows()
    summary = migrate_all_tml_rows()
    assert summary["created"] == 0
    assert summary["unchanged"] == 1
    assert MatchHistory.select().count() == 1


def test_updates_when_source_data_changed():
    row = _tml_create("Player A", "Player B", winner_rank=5)
    migrate_all_tml_rows()
    row.winner_rank = 3
    row.save()
    summary = migrate_all_tml_rows()
    assert summary["updated"] == 1
    stored = MatchHistory.get(MatchHistory.match_id == f"sackmann:{row.id}")
    assert stored.player_a_ranking == 3


def test_does_not_collide_same_pair_multiple_meetings_same_year():
    # Regresión del bug real: 2 encuentros reales distintos entre el
    # mismo par el mismo año NUNCA deben pisarse (id sintético usa el
    # PK real de cada fila, no nombre+año).
    _tml_create("Player A", "Player B", date="2024-05-01", tourney_name="Torneo Uno", score="6-4 6-2")
    _tml_create("Player A", "Player B", date="2024-08-01", tourney_name="Torneo Dos", score="7-5 6-3")
    summary = migrate_all_tml_rows()
    assert summary["created"] == 2
    assert MatchHistory.select().count() == 2
    scores = {r.score for r in MatchHistory.select()}
    assert scores == {"6-4 6-2", "7-5 6-3"}


def test_skips_rows_without_player_names_fail_closed():
    TmlDatabaseMatch.create(year=2024, date="2024-08-01", winner_name="", loser_name="Player B")
    summary = migrate_all_tml_rows()
    assert summary["skipped_invalid"] == 1
    assert MatchHistory.select().count() == 0


def test_winner_side_always_a_for_sackmann_source():
    _tml_create("Winner Real", "Loser Real")
    migrate_all_tml_rows()
    row = MatchHistory.get(MatchHistory.player_a_name == "Winner Real")
    assert row.winner_side == "a"
    assert row.player_b_name == "Loser Real"
    assert row.source == "sackmann"
