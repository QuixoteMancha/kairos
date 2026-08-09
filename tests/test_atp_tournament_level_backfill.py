"""Tests del backfill de `AtpTournamentLevel` -- 2026-08-09, pedido
explícito del usuario: "hay que tener un mapa de torneos, que indique
el level". Voto por MAYORÍA real, no prioridad máxima -- bug real
encontrado en vivo: "Washington" 2025 tenía 43 filas reales '500' + 1
fila mal etiquetada 'M' en la fuente TML-Database misma; prioridad-
máxima elegía la fila mala, mayoría real elige correcto."""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical
from database.historical.tennis.models import AtpTournamentLevel, MatchHistory
from scripts.tennis.atp_tournament_level_backfill import (
    _normalize_tournament_name, compute_level_by_majority_vote, run_backfill,
)

_next_id = iter(range(1, 100_000))


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()
    AtpTournamentLevel.delete().execute()


def _row(tournament, level=None, match_date="2024-05-01"):
    return MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        player_a_name="A", player_b_name="B", match_date=match_date,
        winner_side="a", source="sackmann", tournament=tournament, level=level,
        fetched_at=datetime.now(timezone.utc),
    )


def test_normalize_tournament_name_strips_suffixes_and_numbers():
    assert _normalize_tournament_name("Rome 4 challenger") == "ROME"
    assert _normalize_tournament_name(None) is None


def test_compute_level_by_majority_vote_uses_priority_as_tiebreaker_on_real_tie():
    _row("Rome", level="M", match_date="2024-05-01")
    _row("Rome 2 challenger", level="250", match_date="2024-05-01")
    result = compute_level_by_majority_vote()
    assert result[(2024, "ROME")] == "M"


def test_compute_level_by_majority_vote_uses_majority_not_priority():
    for _ in range(43):
        _row("Washington", level="500", match_date="2025-01-01")
    _row("Washington", level="M", match_date="2025-01-01")
    result = compute_level_by_majority_vote()
    assert result[(2025, "WASHINGTON")] == "500"


def test_run_backfill_persists_into_atp_tournament_level_table():
    _row("Rome", level="M", match_date="2024-05-01")
    result = run_backfill()
    assert result["inserted"] == 1
    row = AtpTournamentLevel.get((AtpTournamentLevel.year == 2024) & (AtpTournamentLevel.tournament_name == "ROME"))
    assert row.level == "M"


def test_run_backfill_updates_existing_entry_when_level_changed():
    AtpTournamentLevel.create(year=2024, tournament_name="ROME", level="250", fetched_at=datetime.now(timezone.utc))
    _row("Rome", level="M", match_date="2024-05-01")
    result = run_backfill()
    assert result["updated"] == 1
    row = AtpTournamentLevel.get((AtpTournamentLevel.year == 2024) & (AtpTournamentLevel.tournament_name == "ROME"))
    assert row.level == "M"


def test_run_backfill_idempotent_on_rerun():
    _row("Rome", level="M", match_date="2024-05-01")
    run_backfill()
    result = run_backfill()
    assert result["unchanged"] == 1
    assert result["inserted"] == 0
    assert AtpTournamentLevel.select().count() == 1
