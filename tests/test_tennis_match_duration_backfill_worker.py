from unittest.mock import patch

from workers.tennis_match_duration_backfill_worker import _run_once


def test_runs_duration_backfill_with_own_limit():
    with patch(
        "workers.tennis_match_duration_backfill_worker.enrich_match_durations",
        return_value={"resolved": 4},
    ) as enrich:
        result = _run_once()

    enrich.assert_called_once_with(limit=40)
    assert result == {"resolved": 4}
