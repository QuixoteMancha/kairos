"""Tests del worker unificado de backfill -- 2026-08-09, pedido
explícito del usuario: "para los 2 scripts y unifica funciones,
revisando el match-detail... derivar/inferir con la data que ya
tenemos"."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory
from scripts.tennis.tennis_match_history_unified_backfill import run_cycle, _bar, print_field_coverage

_next_id = iter(range(1, 100_000))


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()


def _row(source, tour=None, level=None, player_a_ranking=None):
    return MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        player_a_name="A", player_b_name="B", match_date="2024-01-01",
        winner_side="a", source=source, tour=tour, level=level,
        player_a_ranking=player_a_ranking, fetched_at=datetime.now(timezone.utc),
    )


def test_run_cycle_calls_match_detail_and_tour_by_sex_phases():
    with patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_match_detail_phase",
        return_value={"resolved": 1},
    ) as mock_md, patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_tour_by_sex_phase",
        return_value={"players_resolved": 2},
    ) as mock_sex:
        result = run_cycle(cycle_number=1)
    mock_md.assert_called_once()
    mock_sex.assert_called_once()
    assert result["match_detail_phase"] == {"resolved": 1}
    assert result["tour_by_sex_phase"] == {"players_resolved": 2}


def test_run_cycle_runs_derive_phase_only_on_scheduled_cycle():
    with patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_match_detail_phase", return_value={},
    ), patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_tour_by_sex_phase", return_value={},
    ), patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_derive_phase",
        return_value={"rows_resolved": 5},
    ) as mock_derive:
        result_not_scheduled = run_cycle(cycle_number=1)
        result_scheduled = run_cycle(cycle_number=20)
    mock_derive.assert_called_once()  # solo se llamó en el ciclo 20
    assert result_not_scheduled["derive_phase"] == "skipped_this_cycle"
    assert result_scheduled["derive_phase"] == {"rows_resolved": 5}


def test_run_cycle_one_phase_failing_does_not_block_the_others():
    with patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_match_detail_phase",
        side_effect=Exception("red caída"),
    ), patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_tour_by_sex_phase",
        return_value={"players_resolved": 3},
    ) as mock_sex:
        result = run_cycle(cycle_number=1)
    assert "error" in result["match_detail_phase"]
    mock_sex.assert_called_once()
    assert result["tour_by_sex_phase"] == {"players_resolved": 3}


def test_run_cycle_does_not_cap_batch_size():
    # 2026-08-09, pedido explícito del usuario: "tiene que hacer el
    # backfill de los matches que efectivamente carecen de los campos...
    # no de 200 jugadores" -- ambas fases deben llamarse SIN tope
    # artificial (None = procesa todo lo realmente pendiente).
    with patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_match_detail_phase", return_value={},
    ) as mock_md, patch(
        "scripts.tennis.tennis_match_history_unified_backfill.run_tour_by_sex_phase", return_value={},
    ) as mock_sex:
        run_cycle(cycle_number=1)
    mock_md.assert_called_once_with(batch_players=None)
    mock_sex.assert_called_once_with(limit_players=None)


def test_bar_reflects_percentage_filled():
    assert _bar(0.0, width=10) == "[----------]"
    assert _bar(100.0, width=10) == "[##########]"
    assert _bar(50.0, width=10) == "[#####-----]"


def test_bar_clamps_out_of_range_values():
    assert _bar(-10.0, width=10) == "[----------]"
    assert _bar(150.0, width=10) == "[##########]"


def test_print_field_coverage_computes_real_percentage_per_source():
    _row("tennisexplorer", tour="ATP")
    _row("tennisexplorer", tour=None)
    _row("sackmann", level="M", player_a_ranking=5)
    _row("sackmann", level=None, player_a_ranking=None)

    result = print_field_coverage()

    assert result["tennisexplorer"]["tour"] == 50.0
    assert result["sackmann"]["level"] == 50.0
    assert result["sackmann"]["player_a_ranking"] == 50.0


def test_print_field_coverage_handles_empty_source_without_error():
    result = print_field_coverage()
    assert result["tennisexplorer"] == {}
    assert result["sackmann"] == {}
