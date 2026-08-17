from unittest.mock import patch

from workers.tennis_match_stats_backfill_worker import _run_once


_MOD = "workers.tennis_match_stats_backfill_worker"


def test_fetch_saves_typed_stats_without_legacy_rescan():
    with patch(f"{_MOD}.enrich_match_stats", return_value={"resolved": 3}) as fetch:
        result = _run_once()

    fetch.assert_called_once_with(limit=100)
    assert result == {
        "match_stats": {"resolved": 3},
        "match_stats_typed": {"skipped": "typed on write"},
    }


def test_typing_is_skipped_when_network_fetch_fails():
    with patch(f"{_MOD}.enrich_match_stats", side_effect=RuntimeError("network")):
        result = _run_once()

    assert result["match_stats"] == {"error": "RuntimeError: network"}
    assert result["match_stats_typed"] == {"skipped": "typed on write"}
