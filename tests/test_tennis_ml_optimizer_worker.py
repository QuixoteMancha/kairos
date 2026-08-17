from unittest.mock import patch

from workers.tennis_ml_optimizer_worker import _run_once


_MOD = "workers.tennis_ml_optimizer_worker"


def test_skips_training_when_dataset_hash_is_unchanged():
    latest = type("Experiment", (), {"dataset_sha256": "same"})()
    query = type("Query", (), {
        "order_by": lambda self, *args: self,
        "first": lambda self: latest,
    })()
    with patch(f"{_MOD}.collect_dataset", return_value=[{"x": 1}]), patch(
        f"{_MOD}.dataset_sha256", return_value="same",
    ), patch.object(
        __import__("workers.tennis_ml_optimizer_worker", fromlist=["TennisMlExperiment"]).TennisMlExperiment,
        "select", return_value=query,
    ), patch(f"{_MOD}.train_and_evaluate") as train:
        result = _run_once()

    train.assert_not_called()
    assert result["skipped"] == "dataset unchanged"
