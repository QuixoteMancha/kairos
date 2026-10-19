"""Worker summary remains small; full sklearn work is separately tested."""
from unittest.mock import Mock, patch

from workers.tennis_ml_optimizer_worker import _run_once, _run_once_isolated


def _report():
    return {
        "dataset_sha256": "abc", "sample_count": 12, "training_through": "2026-09-09",
        "dataset_quality": {"orientation_pending": 0},
        "models": {"one": {"eligible_for_review": True}, "two": {"eligible_for_review": False}},
    }


def test_run_once_summarizes_direct_optimizer():
    with patch("workers.tennis_ml_optimizer_worker.run_optimizer", return_value=_report()):
        assert _run_once()["eligible_for_review"] == ["one"]


def test_isolated_run_parses_child_report():
    completed = Mock(stdout='{"dataset_sha256":"abc","sample_count":12,"training_through":"2026-09-09","dataset_quality":{"orientation_pending":0},"models":{"one":{"eligible_for_review":true}}}')
    with patch("workers.tennis_ml_optimizer_worker.subprocess.run", return_value=completed) as run:
        result = _run_once_isolated()
    assert result["execution"] == "isolated_subprocess"
    assert result["eligible_for_review"] == ["one"]
    assert run.call_args.kwargs["timeout"] == 7200
