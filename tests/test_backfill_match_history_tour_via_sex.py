"""Tests del backfill de `MatchHistory.tour` vía sexo real de
TennisExplorer -- 2026-08-09, reconstruido tras la migración
`TennisMatchHistory` -> `MatchHistory` (ver docstring del módulo)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory
from scripts.tennis.backfill_match_history_tour_via_sex import run_backfill, _players_missing_tour

_next_id = iter(range(1, 10_000))


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()


def _row(player_a, player_b, tour=None, match_date="2026-01-01"):
    return MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        player_a_name=player_a, player_b_name=player_b, match_date=match_date,
        winner_side="a", tour=tour, source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )


def test_players_missing_tour_collects_both_sides():
    _row("Sabalenka Aryna", "Someone X.")
    _row("Y Player", "Djokovic Novak")
    names = _players_missing_tour()
    assert names == {"Sabalenka Aryna", "Someone X.", "Y Player", "Djokovic Novak"}


def test_players_missing_tour_excludes_already_resolved():
    _row("Sabalenka Aryna", "Someone X.", tour="WTA")
    assert _players_missing_tour() == set()


def test_run_backfill_resolves_and_updates_rows():
    _row("Sabalenka Aryna", "Someone X.")
    _row("Someone X.", "Other Y.")  # mismo jugador "Someone X." en ambos lados de filas distintas
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex",
        side_effect=lambda name: "WTA" if name == "Sabalenka Aryna" else None,
    ), patch("scripts.tennis.backfill_match_history_tour_via_sex.time.sleep"):
        result = run_backfill()
    assert result["players_resolved"] >= 1
    row1 = MatchHistory.get(MatchHistory.player_a_name == "Sabalenka Aryna")
    assert row1.tour == "WTA"


def test_run_backfill_fail_closed_leaves_tour_none_on_unresolved():
    _row("Nobody Real", "Someone X.")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex", return_value=None,
    ), patch("scripts.tennis.backfill_match_history_tour_via_sex.time.sleep"):
        result = run_backfill()
    assert result["players_resolved"] == 0
    assert result["rows_updated"] == 0
    row = MatchHistory.get(MatchHistory.player_a_name == "Nobody Real")
    assert row.tour is None


def test_run_backfill_respects_limit_players():
    _row("A Player", "B Player")
    _row("C Player", "D Player")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex", return_value="ATP",
    ) as mock_fn, patch("scripts.tennis.backfill_match_history_tour_via_sex.time.sleep"):
        run_backfill(limit_players=1)
    assert mock_fn.call_count == 1


def test_run_backfill_skips_already_resolved_rows():
    _row("A Player", "B Player", tour="ATP")
    with patch(
        "scripts.tennis.backfill_match_history_tour_via_sex.get_tour_by_sex",
    ) as mock_fn, patch("scripts.tennis.backfill_match_history_tour_via_sex.time.sleep"):
        run_backfill()
    mock_fn.assert_not_called()
