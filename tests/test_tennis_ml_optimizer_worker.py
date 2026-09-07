from unittest.mock import patch

from workers.tennis_ml_optimizer_worker import _run_once


def test_worker_reports_only_reviewable_challengers():
    report = {
        "dataset_sha256": "abc", "sample_count": 123, "training_through": "2026-09-01",
        "dataset_quality": {"orientation_pending": 2},
        "models": {
            "logistic_regression": {"eligible_for_review": True},
            "random_forest": {"eligible_for_review": False},
        },
    }
    with patch("workers.tennis_ml_optimizer_worker.run_optimizer", return_value=report):
        result = _run_once()

    assert result["eligible_for_review"] == ["logistic_regression"]
    assert result["dataset_quality"]["orientation_pending"] == 2
