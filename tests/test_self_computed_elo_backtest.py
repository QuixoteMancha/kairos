"""Tests del Elo auto-calculado desde TML-Database (2026-08-03, pedido
explícito del usuario -- ver docstring completo en
scripts/self_computed_elo_backtest.py)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.tennis.self_computed_elo_backtest import (
    run_self_computed_elo_backtest, _elo_update, _INITIAL_ELO,
)


def _row(date, winner, winner_rank, loser, loser_rank, best_of=3, surface="Hard"):
    return {
        "tourney_name": "X", "level": "M", "date": date, "round": "F",
        "surface": surface, "best_of": best_of,
        "winner_name": winner, "winner_rank": winner_rank,
        "loser_name": loser, "loser_rank": loser_rank,
        "score": "6-4 6-3", "minutes": 95,
    }


def test_elo_update_winner_gains_loser_loses_symmetric():
    new_w, new_l = _elo_update(_INITIAL_ELO, _INITIAL_ELO)
    assert new_w > _INITIAL_ELO
    assert new_l < _INITIAL_ELO
    assert round(new_w + new_l, 6) == round(_INITIAL_ELO * 2, 6)  # suma cero


def test_elo_update_upset_moves_more_than_expected_win():
    # ganador con Elo MUCHO menor (upset) -> gana más puntos que un
    # ganador esperado (ambos empiezan igual).
    _, upset_delta_loser = _elo_update(1200, 1800)
    expected_w, _ = _elo_update(1500, 1500)
    upset_w, _ = _elo_update(1200, 1800)
    assert (upset_w - 1200) > (expected_w - 1500)


def test_backtest_skips_warmup_matches_from_calibration():
    matches = [
        _row("1999-01-01", "A", 5, "B", 50),  # warmup -- ni siquiera del rango pedido si start=2000
        _row("2015-01-01", "A", 5, "B", 50),  # warmup
        _row("2016-01-01", "A", 5, "B", 50),  # eval
    ]
    with patch(
        "scripts.tennis.self_computed_elo_backtest.get_matches_range", return_value=matches,
    ):
        result = run_self_computed_elo_backtest(warmup_start_year=2000, eval_start_year=2016, end_year=2026)
    assert result["warmup_matches"] == 2  # 1999 y 2015 -- el mock ignora los años pedidos, ambos son < eval_cutoff
    assert result["eval_matches_with_full_data"] == 1


def test_backtest_uses_accumulated_elo_from_warmup_in_eval():
    """El Elo acumulado en el warm-up debe reflejarse en el fair price
    calculado durante la ventana de evaluación -- un jugador (A) que
    ganó muchas veces en el warm-up debería tener un elo_fair_favorite
    claramente alto (>0.70) al llegar a un partido de evaluación donde
    además es favorito por ranking."""
    matches = [_row(f"2010-01-{i:02d}", "A", 5, "B", 50) for i in range(1, 11)]
    matches.append(_row("2016-01-01", "A", 5, "B", 6))  # eval, A sigue siendo favorito por ranking
    with patch(
        "scripts.tennis.self_computed_elo_backtest.get_matches_range", return_value=matches,
    ):
        result = run_self_computed_elo_backtest(warmup_start_year=2000, eval_start_year=2016, end_year=2026)
    elo_calib = result["elo_only_calibration"]
    high_bucket_n = sum(
        b["n"] for label, b in elo_calib.items()
        if not label.startswith(("0.50", "0.55", "0.60", "0.65", "0.70"))
    )
    assert high_bucket_n == 1


def test_backtest_fail_closed_on_invalid_best_of_in_eval_window():
    matches = [_row("2016-01-01", "A", 5, "B", 50, best_of=4)]
    with patch(
        "scripts.tennis.self_computed_elo_backtest.get_matches_range", return_value=matches,
    ):
        result = run_self_computed_elo_backtest(warmup_start_year=2000, eval_start_year=2016, end_year=2026)
    assert result["eval_matches_skipped_invalid_best_of"] == 1
    assert sum(b["n"] for b in result["base_model_calibration"].values()) == 0
    # el Elo-only SÍ se calcula igual (no depende de best_of/pre_match_win_prob)
    assert sum(b["n"] for b in result["elo_only_calibration"].values()) == 1


def test_backtest_skips_eval_matches_without_rank():
    matches = [_row("2016-01-01", "A", None, "B", 50)]
    with patch(
        "scripts.tennis.self_computed_elo_backtest.get_matches_range", return_value=matches,
    ):
        result = run_self_computed_elo_backtest(warmup_start_year=2000, eval_start_year=2016, end_year=2026)
    assert result["eval_matches_skipped_no_rank"] == 1
    assert result["eval_matches_with_full_data"] == 0
