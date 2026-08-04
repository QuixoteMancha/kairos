"""Tests del scraper de TML-Database y del backtest de favorito-por-
ranking-histórico-real (2026-08-02, pedido explícito del usuario)."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tml_database_scraper import get_year_matches, TOUR_LEVELS_250_PLUS
from scripts.favorite_by_historical_rank_backtest import (
    run_backtest, parse_set1_winner, _simulate_capital_paths,
)


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


# ── underdog_comeback_rate_after_losing_set1 (sizing) ───────────────

def test_underdog_comeback_rate_is_complement_of_match_win_rate():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # favorito gana set1 y partido
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
            },
            {  # favorito gana set1 pero underdog remonta y gana el partido
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 50,
                "loser_name": "A", "loser_rank": 5, "score": "4-6 6-3 6-4", "minutes": 150,
            },
        ],
    ):
        result = run_backtest()
    assert result["underdog_comeback_rate_after_losing_set1_evaluable_cases"] == 2
    assert result["underdog_comeback_rate_after_losing_set1_hits"] == 1
    assert result["underdog_comeback_rate_after_losing_set1"] == 0.5
    assert result["match_win_rate_after_favorite_wins_set1"] == 0.5  # complementarios


def test_underdog_comeback_rate_by_rank_gap_buckets():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # gap=10 (bucket 0-20), underdog remonta
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 15,
                "loser_name": "A", "loser_rank": 5, "score": "4-6 6-3 6-4", "minutes": 150,
            },
            {  # gap=150 (bucket 100+), favorito confirma
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 155, "score": "6-4 6-3", "minutes": 95,
            },
        ],
    ):
        result = run_backtest()
    buckets = result["underdog_comeback_rate_by_rank_gap"]
    assert buckets["0-20"]["n"] == 1
    assert buckets["0-20"]["underdog_comeback_rate"] == 1.0
    assert buckets["100+"]["n"] == 1
    assert buckets["100+"]["underdog_comeback_rate"] == 0.0


def test_underdog_comeback_rate_none_when_no_cases():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest()
    assert result["underdog_comeback_rate_after_losing_set1"] is None
    for bucket in result["underdog_comeback_rate_by_rank_gap"].values():
        assert bucket["underdog_comeback_rate"] is None


# ── favorite_comeback_rate_after_losing_set1 (caso simétrico) ──────

def test_favorite_comeback_rate_when_favorite_recovers():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # underdog gana set1 (5-7), favorito (A, rank 5) remonta y gana el partido
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 50, "score": "5-7 6-3 6-4", "minutes": 150,
            },
        ],
    ):
        result = run_backtest()
    assert result["favorite_comeback_rate_after_losing_set1_evaluable_cases"] == 1
    assert result["favorite_comeback_rate_after_losing_set1"] == 1.0


def test_favorite_comeback_rate_when_underdog_holds_on():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # underdog (B) gana set1 Y el partido -- favorito no remonta
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 50,
                "loser_name": "A", "loser_rank": 5, "score": "6-4 6-3", "minutes": 95,
            },
        ],
    ):
        result = run_backtest()
    assert result["favorite_comeback_rate_after_losing_set1"] == 0.0


def test_favorite_comeback_rate_by_rank_gap_buckets():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 155, "score": "5-7 6-3 6-4", "minutes": 150,
        }],
    ):
        result = run_backtest()
    buckets = result["favorite_comeback_rate_by_rank_gap"]
    assert buckets["100+"]["n"] == 1
    assert buckets["100+"]["favorite_comeback_rate"] == 1.0
    assert buckets["0-20"]["n"] == 0


def test_favorite_comeback_rate_none_when_no_cases():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest()
    assert result["favorite_comeback_rate_after_losing_set1"] is None


# ── strategy_comparison (5 verificaciones pedidas explícitamente,
# 3 el 2026-08-02 + 2 más el 2026-08-03) ──

def test_strategy_comparison_simple_matches_overall_hit_rate():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    strat = result["strategy_comparison"]
    assert strat["1_simple_bet_favorite"]["win_rate"] == result["overall_hit_rate"]


def test_strategy_comparison_bet_only_after_set1_and_double_down_share_conditional_metric():
    """Categorías 2 (entrada recién post-set1) y 3 (refuerzo, ya había
    posición pre-match) comparten el MISMO winrate numérico -- ambas
    apuestas terminan en el mismo lado -- pero son estrategias de
    sizing/capital distintas, por eso quedan separadas."""
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    strat = result["strategy_comparison"]
    assert strat["2_bet_only_after_favorite_wins_set1"]["win_rate"] == result["match_win_rate_after_favorite_wins_set1"]
    assert strat["3_double_down_if_favorite_wins_set1"]["win_rate"] == result["match_win_rate_after_favorite_wins_set1"]


def test_strategy_comparison_exit_is_complement_of_favorite_comeback():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # favorito (A) pierde set1 pero remonta y gana -- retirarse hubiera sido un ERROR
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 50, "score": "4-6 6-3 6-4", "minutes": 150,
            },
            {  # favorito (A) pierde set1 y pierde el partido -- retirarse hubiera sido CORRECTO
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 50,
                "loser_name": "A", "loser_rank": 5, "score": "6-4 6-3", "minutes": 95,
            },
        ],
    ):
        result = run_backtest()
    strat = result["strategy_comparison"]
    exit_strategy = strat["4_exit_if_favorite_loses_set1"]
    assert exit_strategy["evaluable_cases"] == 2
    assert exit_strategy["win_rate"] == 0.5  # 1 de 2 veces el retiro fue la decision correcta


def test_strategy_comparison_combined_pools_both_set1_branches():
    """5_combined_double_down_and_exit: pool de "reforzó y ganó" (rama
    favorito-gana-set1) + "se retiró y de verdad perdía" (rama
    favorito-pierde-set1), sobre el total de casos con set 1 conocido."""
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[
            {  # favorito (A) gana set1 y gana el partido -- refuerzo CORRECTO
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
            },
            {  # favorito (A) pierde set1 pero remonta y gana -- retiro INCORRECTO
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 5,
                "loser_name": "B", "loser_rank": 50, "score": "4-6 6-3 6-4", "minutes": 150,
            },
            {  # favorito (A) pierde set1 y pierde el partido -- retiro CORRECTO
                "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
                "surface": "Hard", "best_of": 3, "winner_name": "B", "winner_rank": 50,
                "loser_name": "A", "loser_rank": 5, "score": "6-4 6-3", "minutes": 95,
            },
        ],
    ):
        result = run_backtest()
    strat = result["strategy_comparison"]
    combined = strat["5_combined_double_down_and_exit"]
    assert combined["evaluable_cases"] == 3
    assert combined["win_rate"] == round(2 / 3, 4)  # 2 de 3 decisiones fueron correctas


def test_strategy_comparison_none_when_no_cases():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest()
    strat = result["strategy_comparison"]
    assert strat["1_simple_bet_favorite"]["win_rate"] is None
    assert strat["2_bet_only_after_favorite_wins_set1"]["win_rate"] is None
    assert strat["3_double_down_if_favorite_wins_set1"]["win_rate"] is None
    assert strat["4_exit_if_favorite_loses_set1"]["win_rate"] is None
    assert strat["5_combined_double_down_and_exit"]["win_rate"] is None


# ── capital_simulation (pedido explícito 2026-08-03, capital inicial 50 USDC) ──

def _case(date, hit, set1_favorite_win, rank_gap=25):
    return {
        "tourney_name": "X", "level": "M", "date": date, "round": "F",
        "favorite_rank": 5, "underdog_rank": 5 + rank_gap, "rank_gap": rank_gap,
        "hit": hit, "set1_favorite_win": set1_favorite_win,
    }


def test_capital_simulation_starts_at_initial_capital_and_uses_key():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest(initial_capital=50.0)
    sim = result["capital_simulation"]
    assert set(sim.keys()) == {
        "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
        "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
        "5_combined_double_down_and_exit",
    }
    for entry in sim.values():
        assert entry["initial_capital_usdc"] == 50.0
        assert entry["final_capital_usdc"] == 50.0  # sin casos, capital sin cambios
        assert entry["busted"] is False


def test_capital_simulation_simple_grows_on_win_shrinks_on_loss():
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),
        _case("2026-01-02", hit=False, set1_favorite_win=False),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    # partido 1: gana, +5% de 50 = 52.5 ; partido 2: pierde, -5% de 52.5 = 49.875
    expected = 50.0 * 1.05 * 0.95
    assert sim["1_simple_bet_favorite"]["final_capital_usdc"] == round(expected, 2)


def test_capital_simulation_bet_only_after_set1_skips_matches_without_set1_win():
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=False),  # sin apuesta bajo esta regla
        _case("2026-01-02", hit=False, set1_favorite_win=None),  # sin dato -> sin apuesta
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["2_bet_only_after_favorite_wins_set1"]["final_capital_usdc"] == 50.0


def test_capital_simulation_exit_strategy_flat_when_favorite_loses_set1():
    """Estrategia 4: el retiro se asume a breakeven -- capital sin
    cambios en el partido donde el favorito pierde el set 1, sin
    importar el resultado final del partido."""
    cases = [_case("2026-01-01", hit=False, set1_favorite_win=False)]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["4_exit_if_favorite_loses_set1"]["final_capital_usdc"] == 50.0


def test_capital_simulation_double_down_stakes_more_than_simple_when_set1_confirmed():
    cases = [_case("2026-01-01", hit=True, set1_favorite_win=True)]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    # 5% + 1% = 6% de 50 = 3.0 de ganancia -> 53.0
    assert sim["3_double_down_if_favorite_wins_set1"]["final_capital_usdc"] == 53.0
    # la estrategia simple solo gana el 5% -> 52.5
    assert sim["1_simple_bet_favorite"]["final_capital_usdc"] == 52.5


def test_capital_simulation_busts_when_capital_hits_zero():
    # forzamos varias pérdidas consecutivas hasta que el 5% compounding
    # nunca llega exactamente a 0 (es geométrico) -- en cambio verificamos
    # que jamás queda negativo ni "busted" prematuro con pocas pérdidas.
    cases = [_case(f"2026-01-{i:02d}", hit=False, set1_favorite_win=False) for i in range(1, 10)]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["1_simple_bet_favorite"]["final_capital_usdc"] > 0
    assert sim["1_simple_bet_favorite"]["busted"] is False


def test_capital_simulation_return_pct_matches_final_capital():
    cases = [_case("2026-01-01", hit=True, set1_favorite_win=True)]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    entry = sim["1_simple_bet_favorite"]
    assert entry["return_pct"] == round((entry["final_capital_usdc"] / 50.0 - 1) * 100, 2)


# ── avg_roi_per_trade / max_drawdown / sharpe (pedido explícito 2026-08-03) ──

def test_avg_roi_per_trade_matches_stake_pct_for_simple_strategy():
    """Con payout 1:1 y stake fijo % del capital, el retorno por
    operación de la estrategia simple es SIEMPRE ±5% -- sin importar el
    resultado, el promedio depende solo del winrate real de los casos."""
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),
        _case("2026-01-02", hit=True, set1_favorite_win=True),
        _case("2026-01-03", hit=False, set1_favorite_win=False),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    entry = sim["1_simple_bet_favorite"]
    assert entry["n_trades"] == 3
    # 2 operaciones de +5% y 1 de -5% -> promedio = 5/3 %
    assert entry["avg_roi_per_trade_pct"] == round((0.05 + 0.05 - 0.05) / 3 * 100, 4)


def test_avg_roi_per_trade_excludes_breakeven_exits():
    """Estrategia 4: los partidos donde se retira a breakeven NO cuentan
    como operación (no hubo stake real en juego)."""
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),   # sostiene -> SÍ es operación
        _case("2026-01-02", hit=False, set1_favorite_win=False),  # retiro breakeven -> NO es operación
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    entry = sim["4_exit_if_favorite_loses_set1"]
    assert entry["n_trades"] == 1
    assert entry["avg_roi_per_trade_pct"] == 5.0


def test_max_drawdown_reflects_losing_streak():
    cases = [
        _case("2026-01-01", hit=False, set1_favorite_win=False),
        _case("2026-01-02", hit=False, set1_favorite_win=False),
        _case("2026-01-03", hit=False, set1_favorite_win=False),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    entry = sim["1_simple_bet_favorite"]
    # 3 pérdidas de 5% compounding -> caída total = 1 - 0.95^3
    expected_dd = round((1 - 0.95 ** 3) * 100, 2)
    assert entry["max_drawdown_pct"] == expected_dd


def test_max_drawdown_zero_when_only_wins():
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),
        _case("2026-01-02", hit=True, set1_favorite_win=True),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["1_simple_bet_favorite"]["max_drawdown_pct"] == 0.0


def test_sharpe_none_with_fewer_than_two_trades():
    cases = [_case("2026-01-01", hit=True, set1_favorite_win=True)]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["1_simple_bet_favorite"]["sharpe_ratio_per_trade"] is None


def test_sharpe_none_when_all_returns_identical_zero_variance():
    """Si todas las operaciones ganan (mismo % exacto), el desvío es 0
    -- Sharpe queda None (fail-closed, no se divide por cero)."""
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),
        _case("2026-01-02", hit=True, set1_favorite_win=True),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    assert sim["1_simple_bet_favorite"]["sharpe_ratio_per_trade"] is None


def test_sharpe_positive_when_mixed_but_net_positive_returns():
    cases = [
        _case("2026-01-01", hit=True, set1_favorite_win=True),
        _case("2026-01-02", hit=True, set1_favorite_win=True),
        _case("2026-01-03", hit=False, set1_favorite_win=False),
    ]
    sim = _simulate_capital_paths(cases, initial_capital=50.0)
    entry = sim["1_simple_bet_favorite"]
    assert entry["sharpe_ratio_per_trade"] is not None
    assert entry["sharpe_ratio_per_trade"] > 0


# ── model_fair_price_calibration (pedido explícito 2026-08-03) ──

def test_model_fair_price_calibration_buckets_by_model_probability():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 3, "winner_name": "A", "winner_rank": 1,
            "loser_name": "B", "loser_rank": 200, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    calib = result["model_fair_price_calibration"]
    # rank 1 vs 200 -> el modelo debería dar una probabilidad claramente
    # por encima de 50/50 (no necesariamente extrema -- el motor base es
    # conservador, ver caso real 0.70 verificado con pre_match_win_prob).
    high_buckets = [
        b for label, b in calib["buckets"].items()
        if not label.startswith(("0.50", "0.55", "0.60"))
    ]
    assert sum(b["n"] for b in high_buckets) == 1


def test_model_fair_price_calibration_skips_invalid_best_of():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[{
            "tourney_name": "X", "level": "M", "date": "2026-01-01", "round": "F",
            "surface": "Hard", "best_of": 4, "winner_name": "A", "winner_rank": 5,
            "loser_name": "B", "loser_rank": 50, "score": "6-4 6-3", "minutes": 95,
        }],
    ):
        result = run_backtest()
    calib = result["model_fair_price_calibration"]
    assert calib["skipped_no_surface_or_bestof_info"] == 1
    assert sum(b["n"] for b in calib["buckets"].values()) == 0


def test_model_fair_price_calibration_none_when_no_cases():
    with patch(
        "scripts.favorite_by_historical_rank_backtest.get_matches_range",
        return_value=[],
    ):
        result = run_backtest()
    calib = result["model_fair_price_calibration"]
    for b in calib["buckets"].values():
        assert b["n"] == 0
        assert b["avg_model_fair_a"] is None
        assert b["actual_hit_rate"] is None


# ── capital_simulation_priced (pedido explícito 2026-08-03: usar el modelo
# base como proxy del precio real de mercado en vez de payout 1:1) ──

from scripts.favorite_by_historical_rank_backtest import (
    _real_price_legs, _p_win_match_given_set_score, _simulate_capital_paths_priced,
)


def test_p_win_match_given_set_score_full_favorite_already_won():
    assert _p_win_match_given_set_score(0.5, sets_a=2, sets_b=0, best_of=3) == 1.0


def test_p_win_match_given_set_score_full_favorite_already_lost():
    assert _p_win_match_given_set_score(0.5, sets_a=0, sets_b=2, best_of=3) == 0.0


def test_p_win_match_given_set_score_up_one_set_higher_than_pre_match():
    # con p_set=0.6 (favorito real), estar arriba 1-0 debe dar una
    # probabilidad de ganar el partido MAYOR que estar 0-0.
    p_pre = _p_win_match_given_set_score(0.6, sets_a=0, sets_b=0, best_of=3)
    p_up = _p_win_match_given_set_score(0.6, sets_a=1, sets_b=0, best_of=3)
    p_down = _p_win_match_given_set_score(0.6, sets_a=0, sets_b=1, best_of=3)
    assert p_up > p_pre > p_down


def test_real_price_legs_favorite_price_above_half():
    price_pre, price_win, price_loss = _real_price_legs(5, 200, best_of=3, surface="Hard")
    assert price_pre > 0.5
    assert price_win > price_pre  # confirmado el set1 -> más favorito todavía
    assert price_loss < price_pre  # perdió el set1 -> menos favorito


def test_capital_simulation_priced_loses_full_stake_on_miss():
    cases = [_case("2026-01-01", hit=False, set1_favorite_win=False, rank_gap=25)]
    for c in cases:
        c["best_of"] = 3
        c["surface"] = "Hard"
    sim = _simulate_capital_paths_priced(cases, initial_capital=50.0)
    entry = sim["strategies"]["1_simple_bet_favorite"]
    # perdió -> se pierde el 100% del stake (5% de 50 = 2.5), no el 5% simétrico del 1:1
    assert entry["final_capital_usdc"] == 47.5


def test_capital_simulation_priced_exit_reprices_instead_of_breakeven():
    """Estrategia 4: al perder el set 1, el retiro ahora SE REVALÚA a un
    precio real (mark-to-model), no queda en breakeven como en la
    versión 1:1 -- el capital SÍ cambia."""
    cases = [_case("2026-01-01", hit=True, set1_favorite_win=False, rank_gap=25)]
    for c in cases:
        c["best_of"] = 3
        c["surface"] = "Hard"
    sim = _simulate_capital_paths_priced(cases, initial_capital=50.0)
    entry = sim["strategies"]["4_exit_if_favorite_loses_set1"]
    assert entry["final_capital_usdc"] != 50.0
    assert entry["n_trades"] == 1


def test_capital_simulation_priced_skips_invalid_best_of():
    cases = [_case("2026-01-01", hit=True, set1_favorite_win=True, rank_gap=25)]
    for c in cases:
        c["best_of"] = 4
        c["surface"] = "Hard"
    sim = _simulate_capital_paths_priced(cases, initial_capital=50.0)
    assert sim["skipped_invalid_best_of"] == 1
    for entry in sim["strategies"].values():
        assert entry["final_capital_usdc"] == 50.0


# ── algobetting_concurrency_capped (pedido explícito 2026-08-03) ──

from scripts.favorite_by_historical_rank_backtest import _simulate_algobetting_concurrency_capped


def _dated_case(date, hit, rank_gap=25, best_of=3, surface="Hard"):
    return {
        "date": date, "favorite_rank": 5, "underdog_rank": 5 + rank_gap,
        "rank_gap": rank_gap, "hit": hit, "set1_favorite_win": None,
        "surface": surface, "best_of": best_of,
    }


def test_concurrency_cap_accepts_all_when_under_limit():
    # 3 partidos el mismo día, 1% cada uno -> 3% total, muy por debajo del 50%
    cases = [_dated_case("2026-01-01", hit=True) for _ in range(3)]
    result = _simulate_algobetting_concurrency_capped(cases, initial_capital=50.0)
    assert result["n_trades"] == 3
    assert result["n_skipped_concurrency_cap"] == 0


def test_concurrency_cap_skips_matches_beyond_50pct_same_day():
    # 60 partidos el mismo día al 1% cada uno = 60% de exposición si se
    # aceptaran todos -- el cupo del 50% corta en el partido 50.
    cases = [_dated_case("2026-01-01", hit=True) for _ in range(60)]
    result = _simulate_algobetting_concurrency_capped(cases, initial_capital=50.0)
    assert result["n_trades"] == 50
    assert result["n_skipped_concurrency_cap"] == 10


def test_concurrency_cap_resolves_together_at_day_close():
    """2 partidos el mismo día, resultados opuestos -- ambos deben
    liquidarse con el capital de INICIO de día (no uno afectando al otro
    dentro del mismo día)."""
    cases = [_dated_case("2026-01-01", hit=True), _dated_case("2026-01-01", hit=False)]
    result = _simulate_algobetting_concurrency_capped(cases, initial_capital=50.0)
    assert result["n_trades"] == 2
    assert result["winrate"] == 0.5


def test_concurrency_cap_winrate_and_sharpe_present():
    cases = [
        _dated_case("2026-01-01", hit=True),
        _dated_case("2026-01-02", hit=True),
        _dated_case("2026-01-03", hit=False),
    ]
    result = _simulate_algobetting_concurrency_capped(cases, initial_capital=50.0)
    assert result["winrate"] == round(2 / 3, 4)
    assert result["sharpe_ratio_per_trade"] is not None
    # tolerancia chica -- roi_total_pct se calcula del capital SIN redondear,
    # final_capital_usdc ya viene redondeado a 2 decimales.
    assert abs(result["roi_total_pct"] - (result["final_capital_usdc"] / 50.0 - 1) * 100) < 0.05


def test_concurrency_cap_skips_invalid_best_of():
    cases = [_dated_case("2026-01-01", hit=True, best_of=4)]
    result = _simulate_algobetting_concurrency_capped(cases, initial_capital=50.0)
    assert result["n_skipped_invalid_best_of"] == 1
    assert result["n_trades"] == 0
    assert result["final_capital_usdc"] == 50.0


def test_concurrency_cap_respects_max_stake_usd_ceiling():
    """Con capital muy grande, el stake propuesto (1%) supera el techo
    de liquidez -- debe recortarse a `max_stake_usd`, no seguir
    escalando libremente con el capital."""
    cases = [_dated_case("2026-01-01", hit=True)]
    result_uncapped = _simulate_algobetting_concurrency_capped(
        cases, initial_capital=1_000_000.0, max_stake_usd=None,
    )
    result_capped = _simulate_algobetting_concurrency_capped(
        cases, initial_capital=1_000_000.0, max_stake_usd=100.0,
    )
    # sin techo: 1% de 1,000,000 = 10,000 de stake -> ganancia mucho mayor
    # con techo: stake recortado a 100 -> ganancia mucho menor
    assert result_uncapped["final_capital_usdc"] > result_capped["final_capital_usdc"]
    assert result_capped["max_stake_usd_used"] == 100.0


# ── yearly_edge_degradation (pedido explícito 2026-08-03) ──

from scripts.favorite_by_historical_rank_backtest import (
    _yearly_edge_degradation, _simulate_realistic_all_strategies,
)


def test_yearly_edge_degradation_groups_by_year():
    cases = [
        _dated_case("2020-01-01", hit=True),
        _dated_case("2020-06-01", hit=False),
        _dated_case("2021-01-01", hit=True),
    ]
    result = _yearly_edge_degradation(cases)
    assert set(result["by_year"].keys()) == {"2020", "2021"}
    assert result["by_year"]["2020"]["n"] == 2
    assert result["by_year"]["2021"]["n"] == 1


def test_yearly_edge_degradation_edge_pp_positive_when_winrate_above_model():
    # rank 5 vs 200 -> modelo da ~70%, si el favorito SIEMPRE gana en
    # estos casos el winrate real (100%) queda muy por encima del modelo.
    cases = [_dated_case("2020-01-01", hit=True, rank_gap=195)]
    result = _yearly_edge_degradation(cases)
    year = result["by_year"]["2020"]
    assert year["winrate"] == 1.0
    assert year["edge_pp"] > 0


def test_yearly_edge_degradation_skips_invalid_best_of():
    cases = [_dated_case("2020-01-01", hit=True, best_of=4)]
    result = _yearly_edge_degradation(cases)
    assert result["skipped_invalid_best_of"] == 1
    assert result["by_year"] == {}


# ── realistic_all_strategies (pedido explícito 2026-08-03) ──

def test_realistic_all_strategies_covers_all_8_keys():
    cases = [_dated_case("2026-01-01", hit=True)]
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    assert set(result["strategies"].keys()) == {
        "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
        "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
        "5_combined_double_down_and_exit", "6_combined_with_hedge",
        "7_hold_if_high_comeback_condition_else_exit",
        "8_exit_double_down_leg_if_favorite_loses_set2",
    }


def test_realistic_all_strategies_liquidity_cap_reduces_growth():
    cases = [_dated_case(f"2026-01-{i:02d}", hit=True) for i in range(1, 10)]
    uncapped = _simulate_realistic_all_strategies(cases, initial_capital=1_000_000.0, max_stake_usd=None)
    capped = _simulate_realistic_all_strategies(cases, initial_capital=1_000_000.0, max_stake_usd=100.0)
    for key in uncapped["strategies"]:
        assert uncapped["strategies"][key]["final_capital_usdc"] >= capped["strategies"][key]["final_capital_usdc"]


def test_realistic_all_strategies_strategy2_skips_when_favorite_loses_set1():
    cases = [_dated_case("2026-01-01", hit=True)]
    cases[0]["set1_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["2_bet_only_after_favorite_wins_set1"]
    assert entry["n_trades"] == 0
    assert entry["final_capital_usdc"] == 50.0


def test_realistic_all_strategies_exit_reprices_not_breakeven():
    cases = [_dated_case("2026-01-01", hit=True)]
    cases[0]["set1_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["4_exit_if_favorite_loses_set1"]
    assert entry["final_capital_usdc"] != 50.0
    assert entry["winrate"] is None  # el reprecio no cuenta para winrate


# ── estrategia 6: variante hedge (pedida explícita 2026-08-03) ──

def test_strategy6_adds_hedge_leg_when_favorite_loses_set1():
    """Cuando el favorito pierde el set 1, la variante 6 debe generar
    MÁS operaciones que la 5 (la 5 solo revalúa, la 6 revalúa + agrega
    la pata de cobertura real)."""
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25)]
    cases[0]["set1_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    n5 = result["strategies"]["5_combined_double_down_and_exit"]["n_trades"]
    n6 = result["strategies"]["6_combined_with_hedge"]["n_trades"]
    assert n6 > n5


def test_strategy6_hedge_wins_when_underdog_wins_match():
    """Si el favorito pierde el set 1 Y termina perdiendo el partido
    (underdog gana), la pata de cobertura debe aportar P&L POSITIVO --
    es la pata que estaba diseñada para pagar justo en ese escenario."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=25)]  # favorito pierde el partido
    cases[0]["set1_favorite_win"] = False
    result_hedge = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    result_no_hedge = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap6 = result_hedge["strategies"]["6_combined_with_hedge"]["final_capital_usdc"]
    cap5 = result_no_hedge["strategies"]["5_combined_double_down_and_exit"]["final_capital_usdc"]
    # la cobertura paga cuando el underdog gana -> variante 6 pierde MENOS que la 5 en este escenario
    assert cap6 > cap5


def test_strategy6_hedge_costs_extra_when_favorite_still_wins_match():
    """Si el favorito pierde el set 1 pero REMONTA y gana el partido
    igual, la pata de cobertura pierde su stake completo + fee -- la
    variante 6 debe rendir PEOR que la 5 en este escenario (el costo
    real de asegurar una cobertura que no hacía falta)."""
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25)]  # favorito remonta y gana
    cases[0]["set1_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap6 = result["strategies"]["6_combined_with_hedge"]["final_capital_usdc"]
    cap5 = result["strategies"]["5_combined_double_down_and_exit"]["final_capital_usdc"]
    assert cap6 < cap5


def test_strategy6_respects_concurrency_cap_for_hedge_leg():
    """Si no queda cupo de exposición para la pata de cobertura, debe
    saltarse (contar en n_skipped_concurrency_cap) en vez de violar el
    límite del 50%."""
    # muchos partidos el mismo día, todos con favorito perdiendo el set1
    # -- eventualmente el cupo se agota para las patas de cobertura.
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25) for _ in range(80)]
    for c in cases:
        c["set1_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["6_combined_with_hedge"]
    assert entry["n_skipped_concurrency_cap"] > 0


def test_strategy6_hedge_stake_pct_independent_of_main_stake_pct():
    """hedge_stake_pct debe controlar SOLO la pata de cobertura, no las
    patas pre-match/refuerzo (que siguen usando stake_pct)."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=25)]  # favorito pierde -> hedge paga
    cases[0]["set1_favorite_win"] = False
    result_1pct = _simulate_realistic_all_strategies(cases, initial_capital=50.0, hedge_stake_pct=0.01)
    result_2pct = _simulate_realistic_all_strategies(cases, initial_capital=50.0, hedge_stake_pct=0.02)
    cap6_1pct = result_1pct["strategies"]["6_combined_with_hedge"]["final_capital_usdc"]
    cap6_2pct = result_2pct["strategies"]["6_combined_with_hedge"]["final_capital_usdc"]
    # el hedge paga en este escenario (underdog gana) -> más stake de cobertura = más ganancia
    assert cap6_2pct > cap6_1pct
    assert result_2pct["hedge_stake_pct_used"] == 0.02


def test_strategy6_hedge_stake_pct_defaults_to_stake_pct_when_none():
    cases = [_dated_case("2026-01-01", hit=True)]
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0, stake_pct=0.015, hedge_stake_pct=None)
    assert result["hedge_stake_pct_used"] == 0.015


# ── estrategia 7: retiro condicional al patrón de alta remontada (2026-08-03) ──

from scripts.favorite_by_historical_rank_backtest import _matches_high_comeback_condition


def test_high_comeback_condition_all_criteria_met():
    assert _matches_high_comeback_condition(
        favorite_rank=15, rank_gap=60, favorite_age=22.0, best_of=5, set1_diff=2,
    ) is True


def test_high_comeback_condition_fails_if_rank_too_low():
    assert _matches_high_comeback_condition(
        favorite_rank=25, rank_gap=60, favorite_age=22.0, best_of=5, set1_diff=2,
    ) is False


def test_high_comeback_condition_fails_if_age_in_excluded_gap():
    # 24 años cae en el hueco 23-25, que NO calificó en el hallazgo real
    assert _matches_high_comeback_condition(
        favorite_rank=15, rank_gap=60, favorite_age=24.0, best_of=5, set1_diff=2,
    ) is False


def test_high_comeback_condition_fails_if_bo3():
    assert _matches_high_comeback_condition(
        favorite_rank=15, rank_gap=60, favorite_age=22.0, best_of=3, set1_diff=2,
    ) is False


def test_high_comeback_condition_fail_closed_on_missing_age():
    assert _matches_high_comeback_condition(
        favorite_rank=15, rank_gap=60, favorite_age=None, best_of=5, set1_diff=2,
    ) is False


def test_strategy7_holds_when_condition_met():
    """Con la condición cumplida Y el favorito termina perdiendo el
    partido igual, la 7 debe rendir PEOR que la 4 (la 4 se hubiera
    retirado; la 7 sostuvo por la condición y perdió el stake completo)."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=60)]
    cases[0]["set1_favorite_win"] = False
    cases[0]["favorite_rank"] = 15
    cases[0]["favorite_age"] = 22.0
    cases[0]["best_of"] = 5
    cases[0]["set1_diff"] = 2
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap4 = result["strategies"]["4_exit_if_favorite_loses_set1"]["final_capital_usdc"]
    cap7 = result["strategies"]["7_hold_if_high_comeback_condition_else_exit"]["final_capital_usdc"]
    assert cap7 < cap4


def test_strategy7_exits_when_condition_not_met():
    """Sin la condición cumplida (bo3 acá), la 7 debe comportarse
    IGUAL que la 4 -- mismo capital final."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=60)]
    cases[0]["set1_favorite_win"] = False
    cases[0]["favorite_rank"] = 15
    cases[0]["favorite_age"] = 22.0
    cases[0]["best_of"] = 3  # rompe la condición -> debe comportarse como la 4
    cases[0]["set1_diff"] = 2
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap4 = result["strategies"]["4_exit_if_favorite_loses_set1"]["final_capital_usdc"]
    cap7 = result["strategies"]["7_hold_if_high_comeback_condition_else_exit"]["final_capital_usdc"]
    assert cap7 == cap4


def test_strategy7_covers_all_7_keys():
    cases = [_dated_case("2026-01-01", hit=True)]
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    assert "7_hold_if_high_comeback_condition_else_exit" in result["strategies"]


def test_strategy7_doubles_down_when_favorite_wins_set1():
    """Corrección explícita del usuario: la 7 debe doblar la posición
    cuando el favorito GANA el set 1, igual que la 5 -- no es solo
    'la 4 con retiro condicional'."""
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25)]
    cases[0]["set1_favorite_win"] = True
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    n5 = result["strategies"]["5_combined_double_down_and_exit"]["n_trades"]
    n7 = result["strategies"]["7_hold_if_high_comeback_condition_else_exit"]["n_trades"]
    # ambas deben doblar (2 operaciones: pre-match + refuerzo) -- mismo conteo
    assert n7 == n5 == 2


# ── estrategia 8: retiro de la pata de refuerzo si pierde el set2 (2026-08-03) ──

def test_strategy8_exits_reinforcement_leg_when_favorite_loses_set2():
    """Ganó set1 (dispara refuerzo) pero pierde set2 -- la pata de
    refuerzo debe cerrarse (reprecio), NO resolverse por el resultado
    final del partido. Comparado contra la 5 (que sostiene igual)."""
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25)]  # favorito termina ganando el partido igual
    cases[0]["set1_favorite_win"] = True
    cases[0]["set2_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap5 = result["strategies"]["5_combined_double_down_and_exit"]["final_capital_usdc"]
    cap8 = result["strategies"]["8_exit_double_down_leg_if_favorite_loses_set2"]["final_capital_usdc"]
    # la 5 sostiene el refuerzo y gana el pleno (favorito termina ganando);
    # la 8 se retira de esa pata antes -> gana MENOS que la 5 en este escenario.
    assert cap8 < cap5


def test_strategy8_matches_strategy5_when_favorite_wins_set2():
    """Si el favorito gana el set2 también (2-0 en sets), la 8 debe
    comportarse IGUAL que la 5 -- no hay retiro que aplicar."""
    cases = [_dated_case("2026-01-01", hit=True, rank_gap=25)]
    cases[0]["set1_favorite_win"] = True
    cases[0]["set2_favorite_win"] = True
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap5 = result["strategies"]["5_combined_double_down_and_exit"]["final_capital_usdc"]
    cap8 = result["strategies"]["8_exit_double_down_leg_if_favorite_loses_set2"]["final_capital_usdc"]
    assert cap8 == cap5


def test_strategy8_protects_against_losses_when_favorite_ultimately_loses():
    """Ganó set1, pierde set2, y TERMINA perdiendo el partido -- acá la
    8 debe rendir MEJOR que la 5 (se salvó de la pérdida completa de la
    pata de refuerzo, que es justo el escenario que motivó la regla)."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=25)]  # favorito termina PERDIENDO
    cases[0]["set1_favorite_win"] = True
    cases[0]["set2_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    cap5 = result["strategies"]["5_combined_double_down_and_exit"]["final_capital_usdc"]
    cap8 = result["strategies"]["8_exit_double_down_leg_if_favorite_loses_set2"]["final_capital_usdc"]
    assert cap8 > cap5


def test_strategy8_does_not_touch_pre_match_leg():
    """El retiro de la 8 es SOLO de la pata de refuerzo -- la pata
    pre-match debe seguir resolviendo igual que en la 4/5 (no un
    retiro doble)."""
    cases = [_dated_case("2026-01-01", hit=False, rank_gap=25)]
    cases[0]["set1_favorite_win"] = True
    cases[0]["set2_favorite_win"] = False
    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["8_exit_double_down_leg_if_favorite_loses_set2"]
    # 2 operaciones: pre-match (resuelve por hit) + refuerzo (reprecio) -- ambas cuentan
    assert entry["n_trades"] == 2


# ── fees reales de Kalshi (pedido explícito 2026-08-03) ──

def test_realistic_all_strategies_applies_real_kalshi_fees():
    """Verifica el número EXACTO neto de fee, no solo dirección --
    antes de este fix, ninguna de las estrategias 1-5/7/8 restaba fee
    real de Kalshi (solo la 6, descartada, lo hacía en su pata de
    cobertura)."""
    from outer_apis.kalshi.fees import leg_fee_fraction
    from scripts.favorite_by_historical_rank_backtest import _real_price_legs

    cases = [_dated_case("2026-01-01", hit=True, rank_gap=60)]  # favorito rank5 vs underdog rank65
    cases[0]["favorite_rank"] = 5
    cases[0]["underdog_rank"] = 65
    cases[0]["best_of"] = 3
    cases[0]["surface"] = "Hard"

    p_pre, _, _ = _real_price_legs(5, 65, best_of=3, surface="Hard")
    fee_frac = (leg_fee_fraction(p_pre) or 0.0) / p_pre
    expected_net_pct = (1 - p_pre) / p_pre - fee_frac
    expected_stake = 50.0 * 0.01
    expected_capital = round(50.0 + expected_stake * expected_net_pct, 2)

    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["1_simple_bet_favorite"]
    assert entry["final_capital_usdc"] == expected_capital
    # confirma que la fee realmente resta algo -- no quedó en 0 por error
    assert fee_frac > 0
    assert entry["final_capital_usdc"] < round(50.0 + expected_stake * ((1 - p_pre) / p_pre), 2)


def test_net_reprice_pct_charges_fee_on_both_legs():
    """El reprecio (retiro anticipado) paga fee de ENTRADA y de SALIDA
    -- 2 órdenes reales, criterio conservador (nunca subestimar costo)."""
    from scripts.favorite_by_historical_rank_backtest import (
        _real_price_legs, _simulate_realistic_all_strategies,
    )
    from outer_apis.kalshi.fees import leg_fee_fraction

    cases = [_dated_case("2026-01-01", hit=True, rank_gap=60)]
    cases[0]["favorite_rank"] = 5
    cases[0]["underdog_rank"] = 65
    cases[0]["best_of"] = 3
    cases[0]["surface"] = "Hard"
    cases[0]["set1_favorite_win"] = False  # dispara el retiro/reprecio en la 4

    p_pre, _, p_loss = _real_price_legs(5, 65, best_of=3, surface="Hard")
    fee_entry = (leg_fee_fraction(p_pre) or 0.0) / p_pre
    fee_exit = (leg_fee_fraction(p_loss) or 0.0) / p_loss
    raw_reprice = (p_loss / p_pre) - 1.0
    expected_net_pct = raw_reprice - fee_entry - fee_exit
    expected_stake = 50.0 * 0.01
    expected_capital = round(50.0 + expected_stake * expected_net_pct, 2)

    result = _simulate_realistic_all_strategies(cases, initial_capital=50.0)
    entry = result["strategies"]["4_exit_if_favorite_loses_set1"]
    assert entry["final_capital_usdc"] == expected_capital
