"""Tests del backtest WTA de la estrategia final (2026-08-05, pedido
explícito del usuario -- "calcular por separado los resultados de esta
estrategia tanto en ATP como WTA")."""

from __future__ import annotations

from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis import TennisMatchHistory
from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import (
    _rank_at, _build_surface_winrate_tracker, collect_wta_cases, run_backtest, _MIN_RANK_GAP, _BEST_OF,
    _build_abbrev_to_full_name_index, _resolve_abbreviated_name, _self_won_set1, _simulate_bet_after_set1_win,
)


def setup_function(_fn):
    init_db_historical()
    TennisMatchHistory.delete().execute()


def _real_row(player, opponent, date, result, surface="Hard", score="6-4, 6-3"):
    from datetime import datetime, timezone
    TennisMatchHistory.create(
        player_name=player, opponent_name=opponent, match_date=date, result=result,
        score=score, source="tennisexplorer", surface=surface, fetched_at=datetime.now(timezone.utc),
    )


def test_best_of_is_always_3():
    assert _BEST_OF == 3


def test_min_rank_gap_matches_atp():
    assert _MIN_RANK_GAP == 20


def test_rank_at_finds_most_recent_entry_leq_date():
    index = {"Iga Swiatek": [("2020-01-01", 50), ("2021-01-01", 10), ("2022-01-01", 1)]}
    assert _rank_at("Iga Swiatek", "2021-06-01", index) == 10
    assert _rank_at("Iga Swiatek", "2022-06-01", index) == 1


def test_rank_at_fail_closed_before_first_entry():
    index = {"Iga Swiatek": [("2021-01-01", 10)]}
    assert _rank_at("Iga Swiatek", "2019-01-01", index) is None


def test_rank_at_fail_closed_unknown_player():
    assert _rank_at("Nobody", "2021-01-01", {}) is None


def test_build_abbrev_to_full_name_index_resolves_compound_surname():
    _real_row("Jessica Bouzas Maneiro", "Ivanovic A.", "2016-01-01", "W")
    index = _build_abbrev_to_full_name_index()
    assert index.get("bouzas maneiro j") == "Jessica Bouzas Maneiro"


def test_build_abbrev_to_full_name_index_fail_closed_on_ambiguity():
    # 2 jugadoras reales distintas con EXACTAMENTE el mismo apellido + inicial de nombre
    _real_row("Ana Ivanovic", "X Y.", "2016-01-01", "W")
    _real_row("Anton Ivanovic", "X Y.", "2016-01-01", "W")
    index = _build_abbrev_to_full_name_index()
    assert index.get("ivanovic a") is None  # ambigüedad real, nunca se adivina cuál


def test_resolve_abbreviated_name_fail_closed_bad_format():
    assert _resolve_abbreviated_name("NoDot", {"x y": "X Y"}) is None


def test_collect_wta_cases_excludes_players_outside_roster():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)]}  # "Beatriz Zonta" NO está en el roster de ranking
    _real_row("Ana Ivanovic", "Zonta B.", "2016-01-01", "W")
    _real_row("Beatriz Zonta", "Ivanovic A.", "2016-01-01", "L")  # da universo para resolver "Zonta B."
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=ranking_index,
    ):
        cases, meta = collect_wta_cases()
    assert cases == []
    assert meta["skipped_not_wta_roster"] == 1
    assert meta["skipped_duplicate"] == 1  # la 2da fila es el mismo partido, otra perspectiva


def test_collect_wta_cases_dedupes_same_match_both_perspectives():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 100)]}
    _real_row("Ana Ivanovic", "Kvitova P.", "2016-01-01", "W")  # perspectiva de Ivanovic
    _real_row("Petra Kvitova", "Ivanovic A.", "2016-01-01", "L")  # misma fecha/par, perspectiva de Kvitova
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=ranking_index,
    ):
        cases, meta = collect_wta_cases()
    assert len(cases) == 1
    assert meta["skipped_duplicate"] == 1


def test_surface_winrate_tracker_is_point_in_time_correct():
    cases = [
        {"date": "2016-01-01", "favorite_name": "A", "underdog_name": "Z", "favorite_rank": 5,
         "underdog_rank": 300, "rank_gap": 295, "hit": True, "surface": "Hard"},
        {"date": "2016-02-01", "favorite_name": "Z", "underdog_name": "C", "favorite_rank": 300,
         "underdog_rank": 100, "rank_gap": -200, "hit": False, "surface": "Hard"},
        {"date": "2018-01-01", "favorite_name": "A", "underdog_name": "C", "favorite_rank": 5,
         "underdog_rank": 100, "rank_gap": 95, "hit": True, "surface": "Hard"},
    ]
    result, meta = _build_surface_winrate_tracker(cases)
    case_2018 = next(c for c in result if c["date"] == "2018-01-01")
    assert case_2018["favorite_surface_winrate"] == 1.0  # A: 1-0 en Hard antes de este partido


def test_run_backtest_returns_expected_shape():
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest.collect_wta_cases", return_value=([], {}),
    ):
        result = run_backtest()
    assert result["filtered_cases_n"] == 0
    assert "baseline" in result and "filtered" in result


def test_self_won_set1_uses_self_first_convention():
    # "6-4, 4-6, 6-3" -- self (primer número de cada set) ganó el set 1 6-4
    assert _self_won_set1("6-4, 4-6, 6-3") is True
    assert _self_won_set1("4-6, 6-4, 6-3") is False


def test_self_won_set1_fail_closed():
    assert _self_won_set1(None) is None
    assert _self_won_set1("") is None
    assert _self_won_set1("W/O") is None
    assert _self_won_set1("6-6, 6-3") is None  # set 1 empatado, dato inconsistente


def test_collect_wta_cases_translates_self_won_set1_to_favorite_won_set1():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    # Ivanovic es favorita (rank 5 < 300) y es "self" en esta fila -- ganó el set1 (6-4 self-first)
    _real_row("Ana Ivanovic", "Kvitova P.", "2016-01-01", "W", score="6-4, 6-3")
    _real_row("Petra Kvitova", "Someone X.", "2015-01-01", "L")  # da universo real para resolver "Kvitova P."
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=ranking_index,
    ):
        cases, meta = collect_wta_cases()
    assert cases[0]["favorite_won_set1"] is True


def test_collect_wta_cases_favorite_is_opponent_flips_set1_result():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    # acá Kvitova (opponent, peor rank) es "self" -- pero la FAVORITA es Ivanovic (underdog en la fila, mejor rank)
    # self (Kvitova) ganó el set1 6-4 -- pero la favorita (Ivanovic) NO es self, así que favorite_won_set1 = False
    _real_row("Petra Kvitova", "Ivanovic A.", "2016-01-01", "W", score="6-4, 6-3")
    _real_row("Ana Ivanovic", "Someone X.", "2015-01-01", "L")  # da universo real para resolver "Ivanovic A."
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=ranking_index,
    ):
        cases, meta = collect_wta_cases()
    assert cases[0]["favorite_name"] == "Ana Ivanovic"
    assert cases[0]["favorite_won_set1"] is False


def test_simulate_bet_after_set1_win_only_includes_qualifying_cases():
    cases = [
        {"date": "2016-01-01", "favorite_rank": 5, "underdog_rank": 100, "hit": True,
         "surface": "Hard", "favorite_won_set1": True},
        {"date": "2016-01-02", "favorite_rank": 5, "underdog_rank": 100, "hit": False,
         "surface": "Hard", "favorite_won_set1": False},
        {"date": "2016-01-03", "favorite_rank": 5, "underdog_rank": 100, "hit": True,
         "surface": "Hard", "favorite_won_set1": None},
    ]
    result = _simulate_bet_after_set1_win(cases, initial_capital=50.0, max_stake_usd=None)
    assert result["n_cases"] == 1  # solo el primero (favorite_won_set1 True)
    assert result["n_trades"] == 1


def test_run_backtest_includes_set1_variant():
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest.collect_wta_cases", return_value=([], {}),
    ):
        result = run_backtest()
    assert "filtered_bet_only_after_set1_win" in result
    assert result["filtered_bet_only_after_set1_win"]["n_trades"] == 0
