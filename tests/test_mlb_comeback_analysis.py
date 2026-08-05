"""Tests del análisis de remontadas MLB (2026-08-04, pedido explícito
del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.baseball.mlb_comeback_analysis import (
    _cumulative_runs_through_inning, collect_games_with_linescore, analyze_comeback_rate,
)


def _linescore(innings_runs: list[tuple[int, int]], home_total=None, away_total=None) -> dict:
    innings = [
        {"num": i + 1, "home_runs": h, "away_runs": a}
        for i, (h, a) in enumerate(innings_runs)
    ]
    return {
        "innings": innings,
        "home_runs_total": home_total if home_total is not None else sum(h for h, a in innings_runs),
        "away_runs_total": away_total if away_total is not None else sum(a for h, a in innings_runs),
    }


def test_cumulative_runs_through_inning_sums_correctly():
    ls = _linescore([(0, 1), (2, 0), (0, 0), (1, 0), (0, 2), (1, 0)])
    result = _cumulative_runs_through_inning(ls, 5)
    assert result == (3, 3)  # home: 0+2+0+1+0=3, away: 1+0+0+0+2=3


def test_cumulative_runs_through_inning_fail_closed_if_incomplete():
    ls = _linescore([(0, 1), (2, 0)])  # solo 2 entradas jugadas
    assert _cumulative_runs_through_inning(ls, 5) is None


def test_collect_games_with_linescore_skips_when_linescore_fails():
    fake_games = [{"date": "2025-06-01", "home_team": "A", "away_team": "B", "home_won": True, "game_pk": 1}]
    with patch(
        "scripts.baseball.mlb_comeback_analysis.mlb_stats_scraper.get_games_in_range", return_value=fake_games,
    ), patch(
        "scripts.baseball.mlb_comeback_analysis.mlb_stats_scraper.get_linescore", return_value=None,
    ), patch(
        "scripts.baseball.mlb_comeback_analysis.time.sleep",
    ):
        result = collect_games_with_linescore("2025-06-01", "2025-06-01")
    assert result == []


def test_collect_games_with_linescore_builds_f5_snapshot():
    fake_games = [{"date": "2025-06-01", "home_team": "A", "away_team": "B", "home_won": True, "game_pk": 1}]
    ls = _linescore([(0, 1), (2, 0), (0, 0), (1, 0), (0, 2), (1, 0)])
    with patch(
        "scripts.baseball.mlb_comeback_analysis.mlb_stats_scraper.get_games_in_range", return_value=fake_games,
    ), patch(
        "scripts.baseball.mlb_comeback_analysis.mlb_stats_scraper.get_linescore", return_value=ls,
    ), patch(
        "scripts.baseball.mlb_comeback_analysis.time.sleep",
    ):
        result = collect_games_with_linescore("2025-06-01", "2025-06-01")
    assert len(result) == 1
    assert result[0]["home_runs_f5"] == 3
    assert result[0]["away_runs_f5"] == 3
    assert result[0]["home_runs_final"] == 4
    assert result[0]["away_runs_final"] == 3


def test_analyze_comeback_rate_excludes_ties_at_f5():
    games = [
        {"date": "x", "home_team": "A", "away_team": "B", "home_won": True, "home_runs_f5": 2, "away_runs_f5": 2, "home_runs_final": 3, "away_runs_final": 2},
    ]
    result = analyze_comeback_rate(games)
    assert result["games_tied_at_f5_excluded"] == 1
    assert result["trailing_at_f5_n"] == 0
    assert result["comeback_rate_pct"] is None


def test_analyze_comeback_rate_computes_real_comeback():
    games = [
        # away iba perdiendo 1-3 a la F5, pero termina ganando 5-3 -> remontada
        {"date": "x1", "home_team": "A", "away_team": "B", "home_won": False, "home_runs_f5": 3, "away_runs_f5": 1, "home_runs_final": 3, "away_runs_final": 5},
        # home iba perdiendo 1-2 a la F5 y termina perdiendo 2-4 -> NO remonta
        {"date": "x2", "home_team": "A", "away_team": "B", "home_won": False, "home_runs_f5": 1, "away_runs_f5": 2, "home_runs_final": 2, "away_runs_final": 4},
    ]
    result = analyze_comeback_rate(games)
    assert result["trailing_at_f5_n"] == 2
    assert result["comeback_wins"] == 1
    assert result["comeback_rate_pct"] == 50.0


def test_analyze_comeback_rate_breaks_down_by_margin():
    games = [
        {"date": "x1", "home_team": "A", "away_team": "B", "home_won": True, "home_runs_f5": 1, "away_runs_f5": 0, "home_runs_final": 3, "away_runs_final": 1},
        {"date": "x2", "home_team": "A", "away_team": "B", "home_won": True, "home_runs_f5": 5, "away_runs_f5": 0, "home_runs_final": 6, "away_runs_final": 1},
    ]
    result = analyze_comeback_rate(games)
    assert result["by_margin_at_f5"]["1"]["n"] == 1
    assert result["by_margin_at_f5"]["4+"]["n"] == 1
