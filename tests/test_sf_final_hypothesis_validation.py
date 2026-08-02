"""Tests de `scripts/sf_final_hypothesis_validation.py` (2026-08-02,
validación de la hipótesis SF/Final sobre el dataset completo de
`sf_final_scan.py`, recalculando edge/volatilidad con TennisFModel/
TennisVModel en vez de depender de `LimitArbSignal` real)."""

from __future__ import annotations

import json
from unittest.mock import patch

from scripts.sf_final_hypothesis_validation import validate_hypothesis


def _match(event_ticker, player_a, player_b, price_a, price_b, price_a_max, price_b_max, series="KXATPMATCH"):
    return {
        "event_ticker": event_ticker, "series": series, "round": "Final", "tournament": "Test",
        "player_a": player_a, "player_b": player_b,
        "player_a_price_open": price_a, "player_b_price_open": price_b,
        "player_a_price_max": price_a_max, "player_b_price_max": price_b_max,
    }


def _write_dataset(tmp_path, matches):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(matches), encoding="utf-8")
    return str(path)


def _patched_models(fair_a=0.6, volatility=0.20, rank_a=5, rank_b=50):
    fmodel_patch = patch(
        "scripts.sf_final_hypothesis_validation.TennisFModel.calculate_fair_price",
        return_value={"fair_a_price": fair_a, "fair_b_price": 1 - fair_a},
    )
    vmodel_patch = patch(
        "scripts.sf_final_hypothesis_validation.TennisVModel.calculate_volatility",
        return_value=volatility,
    )
    rank_patch = patch(
        "scripts.sf_final_hypothesis_validation._lookup_rank",
        side_effect=lambda name, cache: rank_a if "A" in name else rank_b,
    )
    rank_cache_patch = patch(
        "scripts.sf_final_hypothesis_validation._rank_lookup", return_value={},
    )
    return fmodel_patch, vmodel_patch, rank_patch, rank_cache_patch


def test_excludes_match_without_price_data(tmp_path):
    dataset = [{"event_ticker": "E1", "series": "KXATPMATCH", "player_a": "A", "player_b": "B",
                "player_a_price_open": None, "player_b_price_open": 0.5}]
    path = _write_dataset(tmp_path, dataset)
    result = validate_hypothesis(path)
    assert result["excluded_no_price_data"] == 1
    assert result["evaluable_cases"] == 0


def test_excludes_match_without_rank_data(tmp_path):
    dataset = [_match("E1", "PlayerA", "PlayerB", 0.3, 0.7, 0.5, 0.9)]
    path = _write_dataset(tmp_path, dataset)
    with patch("scripts.sf_final_hypothesis_validation._rank_lookup", return_value={}), \
         patch("scripts.sf_final_hypothesis_validation._lookup_rank", return_value=None):
        result = validate_hypothesis(path)
    assert result["excluded_no_rank_data"] == 1


def test_hit_when_underdog_price_reaches_target(tmp_path):
    # favorito = A (0.70), fair_a=0.60 -> raw_edge=0.10; underdog B: 0.30
    # target = 0.30 + 0.10 + 0.20/2 = 0.50 -- justo en el límite permitido
    dataset = [_match("E1", "PlayerA", "PlayerB", 0.70, 0.30, 0.75, 0.55)]
    path = _write_dataset(tmp_path, dataset)
    fp, vp, rp, rcp = _patched_models(fair_a=0.60, volatility=0.20)
    with fp, vp, rp, rcp:
        result = validate_hypothesis(path)
    assert result["evaluable_cases"] == 1
    assert result["hits"] == 1
    assert result["cases"][0]["underdog"] == "PlayerB"


def test_miss_when_underdog_never_reaches_target(tmp_path):
    dataset = [_match("E1", "PlayerA", "PlayerB", 0.70, 0.30, 0.75, 0.40)]
    path = _write_dataset(tmp_path, dataset)
    fp, vp, rp, rcp = _patched_models(fair_a=0.60, volatility=0.20)
    with fp, vp, rp, rcp:
        result = validate_hypothesis(path)
    assert result["evaluable_cases"] == 1
    assert result["hits"] == 0


def test_excludes_when_underdog_price_above_40pct(tmp_path):
    dataset = [_match("E1", "PlayerA", "PlayerB", 0.55, 0.45, 0.75, 0.60)]
    path = _write_dataset(tmp_path, dataset)
    fp, vp, rp, rcp = _patched_models(fair_a=0.55, volatility=0.10)
    with fp, vp, rp, rcp:
        result = validate_hypothesis(path)
    assert result["excluded_underdog_above_40pct"] == 1
    assert result["evaluable_cases"] == 0


def test_excludes_when_target_above_50pct(tmp_path):
    dataset = [_match("E1", "PlayerA", "PlayerB", 0.60, 0.20, 0.90, 0.70)]
    path = _write_dataset(tmp_path, dataset)
    # raw_edge = |0.60 - 0.90| = 0.30 -- target = 0.20+0.30+0.30/2=0.65 > 0.5
    fp, vp, rp, rcp = _patched_models(fair_a=0.90, volatility=0.30)
    with fp, vp, rp, rcp:
        result = validate_hypothesis(path)
    assert result["excluded_target_above_50pct"] == 1


def test_hit_rate_none_when_no_evaluable_cases(tmp_path):
    path = _write_dataset(tmp_path, [])
    result = validate_hypothesis(path)
    assert result["hit_rate"] is None
