"""Tests del análisis de patrones de remontada tras perder el set 1
(2026-08-03, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.favorite_comeback_pattern_analysis import (
    run_analysis, _is_retirement_score, _build_retirement_history,
    _had_recent_walkover, _bucket_label,
)


def _row(
    date="2026-01-01", winner="A", winner_rank=5, winner_age=25.0,
    loser="B", loser_rank=50, loser_age=28.0, score="4-6 6-3 6-4",
    round_="F", best_of=3, surface="Hard",
):
    return {
        "tourney_name": "X", "level": "M", "date": date, "round": round_,
        "surface": surface, "best_of": best_of,
        "winner_name": winner, "winner_rank": winner_rank, "winner_age": winner_age,
        "loser_name": loser, "loser_rank": loser_rank, "loser_age": loser_age,
        "score": score, "minutes": 120,
    }


def test_is_retirement_score_detects_ret_and_wo():
    assert _is_retirement_score("6-4 3-1 RET") is True
    assert _is_retirement_score("W/O") is True
    assert _is_retirement_score("6-4 6-3") is False
    assert _is_retirement_score(None) is False


def test_build_retirement_history_only_counts_loser():
    matches = [_row(winner="A", loser="B", score="6-4 3-1 RET", date="2026-01-01")]
    history = _build_retirement_history(matches)
    assert history == {"B": ["2026-01-01"]}


def test_had_recent_walkover_within_window():
    history = {"B": ["2026-01-01"]}
    assert _had_recent_walkover("B", "2026-01-20", history) is True  # 19 días antes
    assert _had_recent_walkover("B", "2026-04-01", history) is False  # muy lejos


def test_had_recent_walkover_never_looks_into_the_future():
    history = {"B": ["2026-06-01"]}
    # el retiro es DESPUÉS del partido en análisis -- no debe contar
    assert _had_recent_walkover("B", "2026-01-01", history) is False


def test_bucket_label_assigns_correct_range():
    buckets = [(1, 11), (11, 21), (21, None)]
    labels = ["Top10", "11-20", "21+"]
    assert _bucket_label(5, buckets, labels) == "Top10"
    assert _bucket_label(15, buckets, labels) == "11-20"
    assert _bucket_label(999, buckets, labels) == "21+"


def test_run_analysis_only_includes_cases_where_favorite_lost_set1():
    matches = [
        _row(winner="A", winner_rank=5, loser="B", loser_rank=50, score="6-4 6-3"),  # favorito ganó set1 -- excluido
        _row(winner="A", winner_rank=5, loser="B", loser_rank=50, score="4-6 6-3 6-4"),  # favorito perdió set1, remontó
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    assert result["n"] == 1
    assert result["overall_comeback_rate_pct"] == 100.0


def test_run_analysis_groups_by_favorite_age():
    matches = [
        # favorito A (winner, rank5) pierde el set1 (4-6) y remonta
        _row(winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=50, score="4-6 6-3 6-4"),
        # favorito A (loser, rank5) pierde el set1 (B gana en sets corridos)
        # y confirma la sorpresa -- misma edad, distinto resultado final.
        _row(winner="B", winner_rank=50, loser="A", loser_rank=5, loser_age=22.0, score="6-4 6-3"),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    assert "<23" in result["by_favorite_age"]
    assert result["by_favorite_age"]["<23"]["n"] == 2


def test_run_analysis_flags_recent_walkover_point_in_time():
    matches = [
        # A se retiró el 2026-01-01
        _row(date="2026-01-01", winner="X", loser="A", winner_rank=1, loser_rank=90, score="4-1 RET"),
        # A es favorito (rank 5) y pierde el set1 el 2026-01-10 (B gana
        # en sets corridos) -- 9 días después del retiro real
        _row(date="2026-01-10", winner="B", winner_rank=50, loser="A", loser_rank=5, score="6-4 6-3"),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    walkover_group = result["by_favorite_recent_walkover"]
    assert walkover_group[True]["n"] == 1


def test_run_analysis_skips_matches_without_rank():
    matches = [_row(winner_rank=None)]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    assert result["skipped_no_rank"] == 1
    assert result["n"] == 0


# ── find_high_comeback_intersection (pedido explícito 2026-08-03) ──

from scripts.favorite_comeback_pattern_analysis import find_high_comeback_intersection


def test_intersection_filters_to_qualifying_buckets_only():
    matches = [
        # favorito Top10 (rank 5), remonta -- alta tasa esperada en ese bucket
        _row(winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=50, score="4-6 6-3 6-4"),
        # favorito 51-100 (rank 60), NO remonta -- bucket de baja tasa
        _row(winner="B", winner_rank=10, loser="A", loser_rank=60, loser_age=22.0, score="6-4 6-3"),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = find_high_comeback_intersection(min_rate_pct=50.0, exclude_categories=(
            "rank_gap_bucket", "favorite_age_bucket", "round", "favorite_had_recent_walkover",
        ))
    # solo el bucket Top10 (100% de remontada, n=1) califica con >=50% -- el otro caso queda afuera
    assert result["intersection_n"] == 1
    assert result["intersection_comeback_rate_pct"] == 100.0


def test_intersection_no_qualifying_bucket_means_no_restriction_on_that_category():
    matches = [_row(winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=50, score="4-6 6-3 6-4")]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = find_high_comeback_intersection(min_rate_pct=99.9, exclude_categories=(
            "favorite_rank_bucket", "rank_gap_bucket", "favorite_age_bucket", "round",
        ))
    # con solo la categoría de walkover activa y umbral altísimo, si ningún
    # bucket califica no debe filtrar nada -> sigue incluyendo el caso.
    assert result["intersection_n"] == 1


def test_intersection_reports_baseline_for_comparison():
    matches = [
        # favorito A (winner, rank5) pierde el set1 y remonta
        _row(winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=50, score="4-6 6-3 6-4"),
        # favorito A (loser, rank10) pierde el set1 (B gana en sets
        # corridos) y NO remonta -- confirma la sorpresa.
        _row(winner="B", winner_rank=60, loser="A", loser_rank=10, loser_age=22.0, score="6-4 6-3"),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = find_high_comeback_intersection(min_rate_pct=30.0)
    assert result["baseline_comeback_rate_pct"] == 50.0  # 1 de 2 remonta en la población total


# ── best_of / set1_game_diff (pedido explícito 2026-08-03) ──

from scripts.favorite_comeback_pattern_analysis import _set1_game_diff


def test_set1_game_diff_computes_absolute_difference():
    assert _set1_game_diff("6-4 6-3") == 2
    assert _set1_game_diff("2-6 6-3 6-4") == 4
    assert _set1_game_diff("6-0 6-1") == 6


def test_set1_game_diff_fail_closed_on_walkover_or_tie():
    assert _set1_game_diff("W/O") is None
    assert _set1_game_diff("6-6 6-3") is None
    assert _set1_game_diff(None) is None


def test_run_analysis_groups_by_best_of():
    matches = [
        _row(winner="A", winner_rank=5, loser="B", loser_rank=50, score="4-6 6-3 6-4", best_of=3),
        _row(winner="B", winner_rank=50, loser="A", loser_rank=5, score="6-4 6-3", best_of=5),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    assert "bo3" in result["by_best_of"]
    assert "bo5" in result["by_best_of"]
    assert result["by_best_of"]["bo3"]["n"] == 1
    assert result["by_best_of"]["bo5"]["n"] == 1


def test_run_analysis_groups_by_set1_closeness():
    matches = [
        _row(winner="A", winner_rank=5, loser="B", loser_rank=50, score="4-6 6-3 6-4"),  # diff=2, cerrado
        _row(winner="B", winner_rank=50, loser="A", loser_rank=5, score="6-0 6-1"),  # diff=6, paliza
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", return_value=matches,
    ):
        result = run_analysis()
    assert result["by_set1_closeness"]["cerrado (1-2 games)"]["n"] == 1
    assert result["by_set1_closeness"]["paliza (5-6 games)"]["n"] == 1


# ── validate_out_of_sample (pedido explícito 2026-08-03, sugerencia #4) ──

from scripts.favorite_comeback_pattern_analysis import validate_out_of_sample


def test_validate_out_of_sample_does_not_recompute_condition_on_test():
    """La condición se encuentra SOLO con datos de train -- nunca debe
    recalcularse con datos de test (eso dejaría de ser validación real)."""
    train_matches = [
        _row(date="2020-01-01", winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=80, score="4-6 6-3 6-4"),
    ]
    test_matches = [
        _row(date="2025-01-01", winner="B", winner_rank=80, loser="A", loser_rank=5, loser_age=22.0, score="6-4 6-3"),
    ]

    def fake_ranged(start_year, end_year, levels=None):
        return train_matches if start_year == 2016 else test_matches

    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range", side_effect=fake_ranged,
    ):
        result = validate_out_of_sample(
            train_start_year=2016, train_end_year=2021,
            test_start_year=2022, test_end_year=2026,
            min_rate_pct=30.0,
        )
    assert result["train_n"] == 1
    assert result["test_n"] == 1
    assert result["test_comeback_rate_pct"] == 0.0  # A perdió en el período de test


def test_validate_out_of_sample_fail_closed_when_test_empty():
    train_matches = [
        _row(date="2020-01-01", winner="A", winner_rank=5, winner_age=22.0, loser="B", loser_rank=80, score="4-6 6-3 6-4"),
    ]
    with patch(
        "scripts.favorite_comeback_pattern_analysis.get_matches_range",
        side_effect=lambda s, e, levels=None: train_matches if s == 2016 else [],
    ):
        result = validate_out_of_sample(
            train_start_year=2016, train_end_year=2021,
            test_start_year=2022, test_end_year=2026,
        )
    assert result["test_n"] == 0
    assert result["test_comeback_rate_pct"] is None
    assert result["holds_up"] is None
