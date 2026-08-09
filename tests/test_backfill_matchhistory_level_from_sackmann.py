"""Tests del backfill UNIFICADO de `MatchHistory.level` (ATP vía
`AtpTournamentLevel` + WTA vía `WtaTournamentLevel`) para filas
TennisExplorer -- 2026-08-09, pedido explícito del usuario:
"unificarlos y añadir el scraping de todos los datos faltantes... el
level de wta" + "hay que tener un mapa de torneos, que indique el
level" (el catálogo ATP ahora es una tabla persistente propia,
`AtpTournamentLevel`, poblada aparte por
`atp_tournament_level_backfill.py` -- ver ese test file para los tests
de cómputo por voto de mayoría)."""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical
from database.historical.tennis.models import AtpTournamentLevel, MatchHistory, WtaTournamentLevel
from scripts.tennis.backfill_matchhistory_level_from_sackmann import (
    _normalize_tournament_name, build_level_index_from_sackmann,
    build_level_index_from_wta_catalog, run_backfill,
)

_next_id = iter(range(1, 100_000))


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()
    WtaTournamentLevel.delete().execute()
    AtpTournamentLevel.delete().execute()


def _row(source, tournament, level=None, match_date="2024-05-01", tour=None, **kw):
    return MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        player_a_name="A", player_b_name="B", match_date=match_date,
        winner_side="a", source=source, tournament=tournament, level=level, tour=tour,
        fetched_at=datetime.now(timezone.utc), **kw,
    )


def _atp_catalog(year, tournament_name, level):
    return AtpTournamentLevel.create(year=year, tournament_name=tournament_name, level=level, fetched_at=datetime.now(timezone.utc))


def test_normalize_tournament_name_strips_suffixes_and_numbers():
    assert _normalize_tournament_name("Rome 4 challenger") == "ROME"
    assert _normalize_tournament_name("Miami WTA") == "MIAMI"
    assert _normalize_tournament_name(None) is None
    assert _normalize_tournament_name("125 ITF") is None  # nada real queda


def test_build_level_index_from_sackmann_reads_atp_tournament_level_table():
    _atp_catalog(2024, "ROME", "M")
    index = build_level_index_from_sackmann()
    assert index == {(2024, "ROME"): "M"}


def test_build_level_index_from_sackmann_skips_rows_without_level():
    _atp_catalog(2024, "MIAMI", None)
    index = build_level_index_from_sackmann()
    assert index == {}


def test_build_level_index_from_wta_catalog_reads_official_roster():
    WtaTournamentLevel.create(year=2024, tournament_name="MADRID", level="Premier Mandatory", fetched_at=datetime.now(timezone.utc))
    index = build_level_index_from_wta_catalog()
    assert index[(2024, "MADRID")] == "Premier Mandatory"


def test_run_backfill_resolves_atp_row_via_sackmann_catalog():
    _atp_catalog(2024, "ROME", "M")
    te_row = _row("tennisexplorer", "Rome 4 challenger", level=None, match_date="2024-05-15", tour="ATP")
    result = run_backfill()
    assert result["rows_resolved"] == 1
    assert result["resolved_atp"] == 1
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "M"


def test_run_backfill_resolves_wta_row_via_official_catalog():
    WtaTournamentLevel.create(year=2024, tournament_name="MADRID", level="Premier Mandatory", fetched_at=datetime.now(timezone.utc))
    te_row = _row("tennisexplorer", "Madrid WTA", level=None, match_date="2024-05-15", tour="WTA")
    result = run_backfill()
    assert result["rows_resolved"] == 1
    assert result["resolved_wta"] == 1
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "Premier Mandatory"


def test_run_backfill_fills_tour_as_bonus_when_tour_unknown_and_unambiguous():
    _atp_catalog(2024, "ROME", "M")
    te_row = _row("tennisexplorer", "Rome", level=None, match_date="2024-05-15", tour=None)
    result = run_backfill()
    assert result["tour_also_filled"] == 1
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "M"
    assert refreshed.tour == "ATP"


def test_run_backfill_fail_closed_on_ambiguous_atp_and_wta_match():
    # mismo (año, torneo normalizado) matchea en AMBOS catálogos -- no
    # se puede saber cuál corresponde, nunca se adivina.
    _atp_catalog(2024, "UNITED CUP", "A")
    WtaTournamentLevel.create(year=2024, tournament_name="UNITED CUP", level="International", fetched_at=datetime.now(timezone.utc))
    te_row = _row("tennisexplorer", "United Cup", level=None, match_date="2024-01-05", tour=None)
    result = run_backfill()
    assert result["resolved_ambiguous_skipped"] == 1
    assert result["rows_resolved"] == 0
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level is None
    assert refreshed.tour is None


def test_run_backfill_fail_closed_when_tournament_not_in_either_catalog():
    te_row = _row("tennisexplorer", "Unknown Small Event", level=None, match_date="2024-05-15", tour=None)
    result = run_backfill()
    assert result["rows_resolved"] == 0
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level is None


def test_run_backfill_fail_closed_when_year_differs():
    _atp_catalog(2024, "ROME", "M")
    te_row = _row("tennisexplorer", "Rome", level=None, match_date="2025-05-15", tour="ATP")
    result = run_backfill()
    assert result["rows_resolved"] == 0
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level is None


def test_run_backfill_never_overwrites_existing_level():
    _atp_catalog(2024, "ROME", "M")
    te_row = _row("tennisexplorer", "Rome", level="250", match_date="2024-05-15", tour="ATP")  # ya tenía algo
    result = run_backfill()
    assert result["rows_resolved"] == 0  # la query ya excluye level.is_null(False)
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "250"  # no se tocó


def test_run_backfill_respects_known_tour_even_if_other_catalog_would_match():
    # tour ya conocido como ATP -- no debe ni mirar el catálogo WTA
    # aunque hipotéticamente matchee, evita falsos positivos por
    # colisión de nombre cuando el tour YA es un hecho confirmado.
    _atp_catalog(2024, "ROME", "M")
    WtaTournamentLevel.create(year=2024, tournament_name="ROME", level="WTA 250", fetched_at=datetime.now(timezone.utc))
    te_row = _row("tennisexplorer", "Rome", level=None, match_date="2024-05-01", tour="ATP")
    result = run_backfill()
    refreshed = MatchHistory.get(MatchHistory.id == te_row.id)
    assert refreshed.level == "M"  # el ATP, no el WTA
