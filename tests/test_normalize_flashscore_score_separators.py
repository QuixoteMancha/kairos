"""Tests de la migración de normalización de separadores de score
(2026-08-06, pedido explícito del usuario -- "aplica un update a los
scores que separe los marcadores de set")."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory
from scripts.tennis.normalize_flashscore_score_separators import run_migration


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def test_run_migration_normalizes_space_separated_flashscore_scores():
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Kudermetova P.", match_date="2026-07-31",
        result="W", score="7-6 6-4", source="flashscore", fetched_at=_now(),
    )
    summary = run_migration()
    assert summary["updated"] == 1
    row = TennisMatchHistory.get(TennisMatchHistory.player_name == "Elina Svitolina")
    assert row.score == "7-6, 6-4"


def test_run_migration_leaves_already_comma_separated_scores_untouched():
    TennisMatchHistory.create(
        player_name="X", opponent_name="Y", match_date="2016-01-01",
        result="W", score="6-4, 3-6, 6-2", source="flashscore", fetched_at=_now(),
    )
    summary = run_migration()
    assert summary["unchanged"] == 1
    assert summary["updated"] == 0
    row = TennisMatchHistory.get(TennisMatchHistory.player_name == "X")
    assert row.score == "6-4, 3-6, 6-2"


def test_run_migration_never_touches_tennisexplorer_rows():
    TennisMatchHistory.create(
        player_name="X", opponent_name="Y", match_date="2016-01-01",
        result="W", score="6-4 3-6 6-2", source="tennisexplorer", fetched_at=_now(),
    )
    summary = run_migration()
    assert summary["total_flashscore_rows"] == 0
    row = TennisMatchHistory.get(TennisMatchHistory.player_name == "X")
    assert row.score == "6-4 3-6 6-2"  # sin tocar -- fuera de alcance a propósito


def test_run_migration_skips_empty_score():
    TennisMatchHistory.create(
        player_name="X", opponent_name="Y", match_date="2016-01-01",
        result="W", score="", source="flashscore", fetched_at=_now(),
    )
    summary = run_migration()
    assert summary["empty_score"] == 1
    assert summary["updated"] == 0


def test_run_migration_dry_run_does_not_write():
    TennisMatchHistory.create(
        player_name="Elina Svitolina", opponent_name="Kudermetova P.", match_date="2026-07-31",
        result="W", score="7-6 6-4", source="flashscore", fetched_at=_now(),
    )
    summary = run_migration(dry_run=True)
    assert summary["updated"] == 1  # se cuenta igual, pero no se escribe
    row = TennisMatchHistory.get(TennisMatchHistory.player_name == "Elina Svitolina")
    assert row.score == "7-6 6-4"  # SIN cambios reales en la base
