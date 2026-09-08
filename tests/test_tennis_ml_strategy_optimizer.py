from __future__ import annotations

from copy import deepcopy

from scripts.tennis.ml_strategy_optimizer import (
    FEATURE_COLUMNS, dataset_sha256, train_and_evaluate,
)


def _rows():
    rows = []
    for year in range(2020, 2027):
        for index in range(120):
            strong = index % 3 != 0
            market = 0.58 + (index % 5) * 0.01
            rows.append({
                "match_id": f"{year}-{index}", "date": f"{year}-06-{index % 28 + 1:02d}",
                "tour": "ATP" if index % 2 else "WTA",
                "surface": "Hard" if index % 3 else "Clay",
                "favorite_decimal_odds": 1.8,
                "market_probability": market,
                "weighted_elo_diff": 120 if strong else -30,
                "surface_elo_diff": 80 if strong else -20,
                "recent_wr_diff": 0.3 if strong else -0.1,
                "elo_slope_5_diff": 8 if strong else -4,
                "elo_slope_10_diff": 5 if strong else -2,
                "service_effectiveness_diff": 6 if strong else None,
                "return_games_rate_diff": 0.1 if strong else None,
                "hit": strong,
            })
    return rows


def test_dataset_hash_includes_features_not_only_match_identity():
    rows = _rows()
    changed = deepcopy(rows)
    changed[0]["weighted_elo_diff"] += 1
    assert dataset_sha256(rows) != dataset_sha256(changed)


def test_trains_all_challengers_and_writes_artifacts(tmp_path):
    report = train_and_evaluate(_rows(), tmp_path)

    assert report["sample_count"] == 840
    assert report["evaluation_assumptions"]["kalshi_fees_applied"] is False
    assert set(report["models"]) == {
        "logistic_regression", "random_forest", "hist_gradient_boosting",
        "evolutionary_rules",
    }
    for name, result in report["models"].items():
        if name == "evolutionary_rules":
            assert result["eligible_for_review"] is False
            continue
        assert [fold["year"] for fold in result["folds"]] == [2025, 2026]
        assert (tmp_path / report["dataset_sha256"] / result["artifact_path"].split("/")[-1]).exists()
        assert result["folds"][0]["brier"] >= 0
        assert "market_favorite_baseline" in result["folds"][0]
        assert "daily_capital_cvar_95_pct" in result["folds"][0]["strategy"]


def test_feature_contract_is_stable():
    assert FEATURE_COLUMNS[:3] == (
        "market_probability", "weighted_elo_diff", "surface_elo_diff",
    )
