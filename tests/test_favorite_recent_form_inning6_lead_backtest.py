"""Tests del backtest MLB "favorito de forma reciente arriba >=3 en la
6ta entrada" (2026-08-05, pedido explícito del usuario -- equivalente a
la variante 2 de tenis, sin exposición pre-partido)."""

from __future__ import annotations

from unittest.mock import patch

from scripts.baseball.favorite_recent_form_inning6_lead_backtest import (
    _build_recent_form_lead_tracker, _margin_bucket, _MIN_LEAD_MARGIN, _INNING_6_CHECKPOINT,
    run_backtest,
)


def _innings(pairs):
    return [{"num": i + 1, "home_runs": h, "away_runs": a} for i, (h, a) in enumerate(pairs)]


def _game(date, home, away, home_won, through6, total_innings=9):
    """`through6`: (home_r6, away_r6) cumulativos reales -- el resto de
    entradas se completa con (0,0) hasta `total_innings` para no afectar
    el checkpoint pero mantener un partido "terminado" real."""
    pairs = [through6] + [(0, 0)] * (total_innings - 1)
    return {"date": date, "home_team": home, "away_team": away, "home_won": home_won, "innings": _innings(pairs)}


def test_margin_bucket_collapses_above_max():
    assert _margin_bucket(3) == 3
    assert _margin_bucket(10) == 7


def test_min_lead_margin_constant_is_3():
    assert _MIN_LEAD_MARGIN == 3


def test_checkpoint_is_inning_6():
    assert _INNING_6_CHECKPOINT == 6


def test_skips_case_without_recent_form_history():
    games = [_game("2020-01-01", "A", "B", True, (4, 0))]
    cases, meta = _build_recent_form_lead_tracker(games)
    assert cases == []
    assert meta["skipped_no_recent_form_history"] == 1


def test_skips_case_when_leading_team_is_not_the_recent_form_favorite():
    # A construye buena forma reciente (5 victorias), B construye mala forma (5 derrotas)
    games = []
    for i in range(5):
        games.append(_game(f"2015-01-{i+1:02d}", "A", "Z", True, (5, 0)))
        games.append(_game(f"2015-02-{i+1:02d}", "Z", "B", True, (5, 0)))
    # eval: B (peor forma) va arriba por 4 en el checkpoint contra A (mejor forma) -- no califica
    games.append(_game("2016-01-01", "A", "B", False, (0, 4)))
    cases, meta = _build_recent_form_lead_tracker(games)
    assert cases == []
    assert meta["skipped_lead_not_by_favorite"] == 1


def test_qualifying_case_needs_bucket_samples_before_pricing():
    """Sin suficientes muestras REALES previas en el bucket de margen,
    el caso se descarta (fail-closed) aunque el resto de condiciones
    califique."""
    games = []
    for i in range(5):
        games.append(_game(f"2015-01-{i+1:02d}", "A", "Z", True, (5, 0)))
        games.append(_game(f"2015-02-{i+1:02d}", "Z", "B", True, (5, 0)))  # B pierde de visitante -- peor forma reciente que A
    # eval: A (mejor forma) va arriba por 4 -- califica salvo por bucket_samples (nuevo bucket, 0 muestras previas)
    games.append(_game("2016-01-01", "A", "B", True, (4, 0)))
    cases, meta = _build_recent_form_lead_tracker(games)
    assert cases == []
    assert meta["skipped_no_bucket_samples"] >= 1


def test_run_backtest_returns_expected_shape():
    with patch(
        "scripts.baseball.favorite_recent_form_inning6_lead_backtest.collect_games_with_innings_from_db",
        return_value=[],
    ):
        result = run_backtest()
    assert result["n_qualifying_cases"] == 0
    assert "result" in result
    assert result["result"]["n_trades"] == 0
