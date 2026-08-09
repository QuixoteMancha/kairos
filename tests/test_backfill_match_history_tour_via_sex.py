"""Tests del backfill de `tour` vía sexo (TennisExplorer) -- 2026-08-09,
pedido explícito del usuario."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory
from scripts.tennis.backfill_match_history_tour_via_sex import run_backfill


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _mh(match_id, a, b, tour=None):
    return MatchHistory.create(
        match_id=match_id, source="tennisexplorer", tour=tour,
        player_a_name=a, player_b_name=b, fetched_at=_now(),
    )


def test_resolves_tour_for_rows_missing_it():
    _mh("m1", "Aryna Sabalenka", "Amanda Anisimova")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex",
        side_effect=lambda name: "WTA" if "Sabalenka" in name or "Anisimova" in name else None,
    ):
        summary = run_backfill()
    assert summary["match_history_rows_updated"] == 1
    row = MatchHistory.get(MatchHistory.match_id == "m1")
    assert row.tour == "WTA"


def test_skips_rows_that_already_have_tour():
    _mh("m1", "Aryna Sabalenka", "Amanda Anisimova", tour="WTA")
    with patch("scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex") as mock_fn:
        summary = run_backfill()
    mock_fn.assert_not_called()
    assert summary["match_history_rows_updated"] == 0


def test_does_not_refetch_same_player_across_multiple_matches():
    _mh("m1", "Aryna Sabalenka", "Player A")
    _mh("m2", "Aryna Sabalenka", "Player B")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex", return_value="WTA",
    ) as mock_fn:
        summary = run_backfill()
    # 3 jugadores únicos totales (Sabalenka, Player A, Player B) -- 1 sola
    # llamada por persona, nunca repetida aunque Sabalenka esté en 2 partidos.
    assert mock_fn.call_count == 3
    assert summary["match_history_rows_updated"] == 2


def test_leaves_row_unresolved_when_neither_player_resolves():
    _mh("m1", "Nobody Known", "Also Nobody")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex", return_value=None,
    ):
        summary = run_backfill()
    assert summary["match_history_rows_updated"] == 0
    row = MatchHistory.get(MatchHistory.match_id == "m1")
    assert row.tour is None


def test_resolves_via_either_player_side():
    _mh("m1", "Player Unknown", "Novak Djokovic")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex",
        side_effect=lambda name: "ATP" if name == "Novak Djokovic" else None,
    ):
        summary = run_backfill()
    assert summary["match_history_rows_updated"] == 1
    row = MatchHistory.get(MatchHistory.match_id == "m1")
    assert row.tour == "ATP"
