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
                "recent_point_share_diff": 0.08 if strong else -0.03,
                "recent_return_points_effectiveness_diff": 7 if strong else -3,
                "favorite_tournament_minutes": 180.0,
                "underdog_tournament_minutes": 130.0,
                "tournament_minutes_diff": 50.0,
                "favorite_tournament_matches": 2,
                "underdog_tournament_matches": 2,
                "tournament_matches_diff": 0,
                "tournament_stage": "Quarterfinal",
                "statistics_evidence_complete": True,
                "hit": strong,
            })
    return rows


def test_dataset_hash_includes_features_not_only_match_identity():
    rows = _rows()
    changed = deepcopy(rows)
    changed[0]["weighted_elo_diff"] += 1
    assert dataset_sha256(rows) != dataset_sha256(changed)


def test_dataset_hash_includes_supervised_outcome_corrections():
    rows = _rows()
    corrected = deepcopy(rows)
    corrected[0]["hit"] = not corrected[0]["hit"]

    assert dataset_sha256(rows) != dataset_sha256(corrected)


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
        assert result["validation_complete"] is True
        assert result["artifact_training_through"] == "2026-06-28"
        assert result["execution_ready"] is False
        assert result["review_scope"] == "research_proxy_pricing_only"
        assert (tmp_path / report["dataset_sha256"] / result["artifact_path"].split("/")[-1]).exists()
        assert result["folds"][0]["brier"] >= 0
        assert "market_favorite_baseline" in result["folds"][0]
        assert "daily_capital_cvar_95_pct" in result["folds"][0]["strategy"]


def test_feature_contract_is_stable():
    assert FEATURE_COLUMNS[:3] == (
        "market_probability", "weighted_elo_diff", "surface_elo_diff",
    )


def test_empty_dataset_reports_insufficient_data_without_crashing(tmp_path):
    report = train_and_evaluate([], tmp_path)

    assert report["sample_count"] == 0
    assert report["training_through"] is None
    for name in ("logistic_regression", "random_forest", "hist_gradient_boosting"):
        assert report["models"][name] == {
            "folds": [], "eligible_for_review": False, "reason": "insufficient data",
        }


def test_incomplete_rolling_validation_cannot_be_reviewable(tmp_path):
    # 2025 has only 480 prior rows, while 2026 has 600.  This used to leave
    # a single apparently successful fold eligible for review.
    rows = [row for row in _rows() if row["date"][:4] >= "2021"]
    report = train_and_evaluate(rows, tmp_path)

    for name in ("logistic_regression", "random_forest", "hist_gradient_boosting"):
        result = report["models"][name]
        assert [fold["year"] for fold in result["folds"]] == [2026]
        assert result["validation_complete"] is False
        assert result["eligible_for_review"] is False
