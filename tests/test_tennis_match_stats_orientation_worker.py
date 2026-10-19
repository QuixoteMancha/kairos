from unittest.mock import Mock, patch

from workers import tennis_match_stats_orientation_worker as worker


def test_completion_training_uses_the_isolated_optimizer_child():
    pending_query = Mock()
    pending_query.where.return_value.count.return_value = 0
    experiment_query = Mock()
    experiment_query.where.return_value.exists.return_value = False
    report = {"dataset_sha256": "data", "sample_count": 42}

    with patch.object(worker.MatchStats, "select", return_value=pending_query), \
         patch.object(worker.TennisMlExperiment, "select", return_value=experiment_query), \
         patch.object(worker, "_run_once_isolated", return_value=report) as run:
        result = worker._train_when_orientation_complete()

    assert result == {
        "triggered": True, "orientation_pending": 0,
        "dataset_sha256": "data", "sample_count": 42,
    }
    run.assert_called_once_with()


def test_completion_training_does_not_repeat_existing_proxy_experiment():
    pending_query = Mock()
    pending_query.where.return_value.count.return_value = 0
    experiment_query = Mock()
    experiment_query.where.return_value.exists.return_value = True

    with patch.object(worker.MatchStats, "select", return_value=pending_query), \
         patch.object(worker.TennisMlExperiment, "select", return_value=experiment_query), \
         patch.object(worker, "_run_once_isolated") as run:
        result = worker._train_when_orientation_complete()

    assert result == {
        "triggered": False, "orientation_pending": 0, "reason": "already_trained",
    }
    run.assert_not_called()
