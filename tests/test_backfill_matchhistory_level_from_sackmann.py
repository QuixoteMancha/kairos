"""Tests del backfill de `MatchHistory.level` para filas TennisExplorer,
derivado del catálogo ya presente en filas Sackmann -- 2026-08-09,
pedido explícito del usuario: "tennisexplorer expone el torneo, se
puede hacer un map para derivar/deducir el level del torneo"."""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory
from scripts.tennis.backfill_matchhistory_level_from_sackmann import (
    _normalize_tournament_name, build_level_index_from_sackmann, run_backfill,
)

_next_id = iter(range(1, 100_000))


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()


def _row(source, tournament, level=None, match_date="2024-05-01", **kw):
    return MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        player_a_name="A", player_b_name="B", match_date=match_date,
        winner_side="a", source=source, tournament=tournament, level=level,
        fetched_at=datetime.now(timezone.utc), **kw,
    )


def test_normalize_tournament_name_strips_suffixes_and_numbers():
    assert _normalize_tournament_name("Rome 4 challenger") == "ROME"
    assert _normalize_tournament_name("Miami WTA") == "MIAMI"
    assert _normalize_tournament_name(None) is None
    assert _normalize_tournament_name("125 ITF") is None  # nada real queda


def test_build_level_index_reads_only_sackmann_rows_with_level():
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    _row("sackmann", "Miami", level=None, match_date="2024-03-01")  # sin level, no debe indexarse
    _row("tennisexplorer", "Rome", level=None, match_date="2024-05-01")  # no es sackmann, no debe indexarse
    index = build_level_index_from_sackmann()
    assert index == {(2024, "ROME"): "M"}


def test_build_level_index_keeps_higher_priority_on_collision():
    # "Rome" (Masters) y "Rome 2 challenger" (nivel 250, colisiona tras
    # normalizar a "ROME") -- debe conservar el de mayor jerarquía real.
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    _row("sackmann", "Rome 2 challenger", level="250", match_date="2024-05-01")
    index = build_level_index_from_sackmann()
    assert index[(2024, "ROME")] == "M"


def test_run_backfill_resolves_matching_tennisexplorer_rows():
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    te_row = _row("tennisexplorer", "Rome 4 challenger", level=None, match_date="2024-05-15")
    result = run_backfill()
    assert result["rows_resolved"] == 1
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "M"


def test_run_backfill_fail_closed_when_tournament_not_in_sackmann():
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    te_row = _row("tennisexplorer", "Unknown Small Event", level=None, match_date="2024-05-15")
    result = run_backfill()
    assert result["rows_resolved"] == 0
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level is None


def test_run_backfill_fail_closed_when_year_differs():
    # mismo torneo, año distinto -- categoría puede haber cambiado, no se asume.
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    te_row = _row("tennisexplorer", "Rome", level=None, match_date="2025-05-15")
    result = run_backfill()
    assert result["rows_resolved"] == 0
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level is None


def test_run_backfill_never_overwrites_existing_level():
    _row("sackmann", "Rome", level="M", match_date="2024-05-01")
    te_row = _row("tennisexplorer", "Rome", level="250", match_date="2024-05-15")  # ya tenía algo
    result = run_backfill()
    assert result["rows_resolved"] == 0  # la query ya excluye level.is_null(False)
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "250"  # no se tocó
