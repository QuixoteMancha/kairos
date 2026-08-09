"""Tests del worker unificado de backfill -- 2026-08-09, pedido
explícito del usuario: "para los 2 scripts y unifica funciones,
revisando el match-detail... derivar/inferir con la data que ya
tenemos"."""

from __future__ import annotations

from unittest.mock import patch

from scripts.tennis.tennis_match_history_unified_backfill import run_cycle


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
