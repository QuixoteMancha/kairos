"""Tests del scraper de TML-Database y del backtest de favorito-por-
ranking-histórico-real (2026-08-02, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tml_database_scraper import get_year_matches, TOUR_LEVELS_250_PLUS
from scripts.favorite_by_historical_rank_backtest import run_backtest, parse_set1_winner


def _fake_csv_rows(*rows: dict) -> list[dict]:
    return list(rows)


def _row(**overrides):
    base = {
        "tourney_name": "Miami Masters", "tourney_level": "M", "tourney_date": "20260322",
        "round": "F", "surface": "Hard", "best_of": "3",
        "winner_name": "Player A", "winner_rank": "5",
        "loser_name": "Player B", "loser_rank": "50",
        "score": "6-4 6-3", "minutes": "95",
    }
    base.update(overrides)
    return base


def test_get_year_matches_filters_by_level():
    rows = [_row(tourney_level="M"), _row(tourney_level="D"), _row(tourney_level="A")]
    with patch(
        "outer_apis.scrapers.tennis.tml_database_scraper._fetch_csv", return_value=rows,
    ):
        result = get_year_matches(2026, levels=TOUR_LEVELS_250_PLUS)
    assert len(result) == 1
    assert result[0]["level"] == "M"


def test_get_year_matches_parses_real_fields():
    with patch(
        "outer_apis.scrapers.tennis.tml_database_scraper._fetch_csv", return_value=[_row()],
    ):
        result = get_year_matches(2026, levels=None)
    m = result[0]
    assert m["winner_rank"] == 5
    assert m["loser_rank"] == 50
    assert m["date"] == "2026-03-22"
    assert m["minutes"] == 95


def test_get_year_matches_fail_closed_on_fetch_failure():
    with patch("outer_apis.scrapers.tennis.tml_database_scraper._fetch_csv", return_value=None):
        assert get_year_matches(2026) == []


def test_get_year_matches_skips_rows_missing_names_or_date():
    rows = [_row(winner_name=""), _row(tourney_date="bad")]
    with patch("outer_apis.scrapers.tennis.tml_database_scraper._fetch_csv", return_value=rows):
        assert get_year_matches(2026, levels=None) == []


def test_backtest_hit_when_better_ranked_wins():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "Miami Masters", "level": "M", "date": "2026-03-22", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    assert result["evaluable_cases"] == 1
    assert result["overall_hit_rate"] == 1.0
    assert result["by_level"]["M"]["hit_rate"] == 1.0
    assert result["by_round"]["F"]["hit_rate"] == 1.0


def test_backtest_miss_when_underdog_wins():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "250", "date": "2026-01-01", "round": "R32",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 90,
            "loser_name": "B", "loser_rank": 5, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    assert result["overall_hit_rate"] == 0.0


def test_backtest_excludes_matches_without_rank():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "250", "date": "2026-01-01", "round": "R32",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": None,
            "loser_name": "B", "loser_rank": 5, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    assert result["excluded_no_rank"] == 1
    assert result["evaluable_cases"] == 0
    assert result["overall_hit_rate"] is None


# ── parse_set1_winner ────────────────────────────────────────────────

def test_parse_set1_winner_match_winner_won_set1():
    assert parse_set1_winner("6-4 6-3") == "winner"


def test_parse_set1_winner_match_winner_lost_set1_comeback():
    assert parse_set1_winner("0-6 6-4 6-4") == "loser"


def test_parse_set1_winner_with_tiebreak_notation():
    assert parse_set1_winner("7-6(5) 6-3") == "winner"


def test_parse_set1_winner_fail_closed_on_walkover():
    assert parse_set1_winner("W/O") is None
    assert parse_set1_winner("") is None
    assert parse_set1_winner(None) is None


def test_parse_set1_winner_fail_closed_on_tied_score():
    assert parse_set1_winner("6-6 6-3") is None


# ── set1_favorite_win_rate en el backtest ───────────────────────────

def test_backtest_set1_favorite_win_rate_when_favorite_wins_both():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    assert result["set1_evaluable_cases"] == 1
    assert result["set1_favorite_win_rate"] == 1.0


def test_backtest_set1_favorite_loses_set1_but_wins_match():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "3-6 6-4 6-4", "minutes": 150,
        }],
    ):
        result = run_backtest()
    assert result["overall_hit_rate"] == 1.0  # el favorito ganó el partido
    assert result["set1_favorite_win_rate"] == 0.0  # pero perdió el 1er set


def test_backtest_set1_excludes_walkovers():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "W/O", "minutes": None,
        }],
    ):
        result = run_backtest()
    assert result["set1_evaluable_cases"] == 0
    assert result["set1_favorite_win_rate"] is None


# ── match_win_rate_after_favorite_wins_set1 ─────────────────────────

def test_conditional_match_winrate_after_set1_all_confirm():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    assert result["match_win_rate_after_favorite_wins_set1_evaluable_cases"] == 1
    assert result["match_win_rate_after_favorite_wins_set1"] == 1.0


def test_conditional_match_winrate_after_set1_favorite_chokes():
    # el favorito (rank 5) gana el set 1 (6-4) pero termina PERDIENDO el
    # partido (underdog, rank 50, remonta y gana) -- caso real posible.
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 50,
            "loser_name": "A", "loser_rank": 5, "score": "4-6 6-3 6-4", "minutes": 150,
        }],
    ):
        result = run_backtest()
    # score winner-first: "4-6" -> match winner (B, underdog) LOST set 1
    # -> set1 lo ganó el favorito (A) -- pero A perdio el partido.
    assert result["match_win_rate_after_favorite_wins_set1_evaluable_cases"] == 1
    assert result["match_win_rate_after_favorite_wins_set1"] == 0.0


def test_conditional_match_winrate_none_when_no_evaluable_cases():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest()
    assert result["match_win_rate_after_favorite_wins_set1"] is None


# ── match_win_rate_big_rank_gap_and_won_set1 ────────────────────────

def test_big_rank_gap_included_when_gap_at_least_20():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 25, "score": "6-4 6-3", "minutes": 95,  # gap=20
        }],
    ):
        result = run_backtest()
    assert result["match_win_rate_big_rank_gap_and_won_set1_evaluable_cases"] == 1
    assert result["match_win_rate_big_rank_gap_and_won_set1"] == 1.0
    assert result["min_rank_gap_used"] == 20


def test_small_rank_gap_excluded():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 24, "score": "6-4 6-3", "minutes": 95,  # gap=19
        }],
    ):
        result = run_backtest()
    assert result["match_win_rate_big_rank_gap_and_won_set1_evaluable_cases"] == 0
    assert result["match_win_rate_big_rank_gap_and_won_set1"] is None
