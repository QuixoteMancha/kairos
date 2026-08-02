"""Tests de `scripts/favorite_strategy_backtest.py` (2026-08-02, pedido
explícito del usuario: estrategia de 'apostar siempre al favorito' en
TODAS las rondas, no solo SF/Final)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.favorite_strategy_backtest import run_favorite_strategy_backtest


def _leg(ticker, title, event_ticker, result=None):
    return {
        "id": ticker,
        "raw": {"event_ticker": event_ticker, "title": title, "yes_sub_title": None, "result": result},
    }


def _fake_api(markets_by_series):
    api = MagicMock()
    api.get_markets.side_effect = lambda status, series_ticker, max_pages: markets_by_series.get(series_ticker, [])
    return api


def test_favorite_by_rank_wins_counts_as_hit():
    legs = [
        _leg("T1-A", "Will A win the A vs B: R32 match?", "EVT1", result="yes"),
        _leg("T1-B", "Will B win the A vs B: R32 match?", "EVT1", result="no"),
    ]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}), \
         patch("scripts.favorite_strategy_backtest._lookup_rank", side_effect=lambda name, c: 5 if name == "A" else 50):
        result = run_favorite_strategy_backtest()

    assert result["total_evaluable"] == 1
    assert result["overall_hits"] == 1
    assert result["by_round"]["R32"]["hit_rate"] == 1.0


def test_favorite_loses_counts_as_miss():
    legs = [
        _leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", result="no"),
        _leg("T1-B", "Will B win the A vs B: Final match?", "EVT1", result="yes"),
    ]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}), \
         patch("scripts.favorite_strategy_backtest._lookup_rank", side_effect=lambda name, c: 5 if name == "A" else 50):
        result = run_favorite_strategy_backtest()

    assert result["overall_hits"] == 0
    assert result["overall_hit_rate"] == 0.0


def test_excludes_when_rank_missing():
    legs = [
        _leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", result="yes"),
        _leg("T1-B", "Will B win the A vs B: Final match?", "EVT1", result="no"),
    ]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}), \
         patch("scripts.favorite_strategy_backtest._lookup_rank", return_value=None):
        result = run_favorite_strategy_backtest()

    assert result["total_evaluable"] == 0
    assert result["excluded_no_rank"] == 1


def test_excludes_incomplete_events():
    legs = [_leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", result="yes")]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}):
        result = run_favorite_strategy_backtest()

    assert result["excluded_incomplete_event"] == 1


def test_excludes_when_no_winner_yet():
    legs = [
        _leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", result=None),
        _leg("T1-B", "Will B win the A vs B: Final match?", "EVT1", result=None),
    ]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}):
        result = run_favorite_strategy_backtest()

    assert result["excluded_no_winner"] == 1


def test_breaks_down_by_round():
    legs = [
        _leg("T1-A", "Will A win the A vs B: R32 match?", "EVT1", result="yes"),
        _leg("T1-B", "Will B win the A vs B: R32 match?", "EVT1", result="no"),
        _leg("T2-A", "Will C win the C vs D: Final match?", "EVT2", result="no"),
        _leg("T2-B", "Will D win the C vs D: Final match?", "EVT2", result="yes"),
    ]
    api = _fake_api({"KXATPMATCH": legs, "KXWTAMATCH": []})
    with patch("scripts.favorite_strategy_backtest.KalshiAPI", return_value=api), \
         patch("scripts.favorite_strategy_backtest._rank_lookup", return_value={}), \
         patch(
             "scripts.favorite_strategy_backtest._lookup_rank",
             side_effect=lambda name, c: {"A": 5, "B": 50, "C": 5, "D": 50}[name],
         ):
        result = run_favorite_strategy_backtest()

    assert result["by_round"]["R32"]["hit_rate"] == 1.0
    assert result["by_round"]["Final"]["hit_rate"] == 0.0
    assert result["overall_hit_rate"] == 0.5
