from unittest.mock import patch

from workers.tennis_match_stats_backfill_worker import _run_once


_MOD = "workers.tennis_match_stats_backfill_worker"


def test_runs_fetch_and_typed_backfills_in_one_cycle():
    with patch(f"{_MOD}.enrich_match_stats", return_value={"resolved": 3}) as fetch, patch(
        f"{_MOD}.backfill_typed_match_stats", return_value={"updated": 3},
    ) as typed:
        result = _run_once()

    fetch.assert_called_once_with(limit=100)
    typed.assert_called_once_with(limit=500)
    assert result == {
        "match_stats": {"resolved": 3},
        "match_stats_typed": {"updated": 3},
    }


def test_typing_is_skipped_when_network_fetch_fails():
    with patch(f"{_MOD}.enrich_match_stats", side_effect=RuntimeError("network")), patch(
        f"{_MOD}.backfill_typed_match_stats", return_value={"updated": 2},
    ) as typed:
        result = _run_once()

    typed.assert_not_called()
    assert result["match_stats"] == {"error": "RuntimeError: network"}
    assert result["match_stats_typed"] == {"skipped": "no new stats"}
