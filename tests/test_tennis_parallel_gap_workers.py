from unittest.mock import patch


def test_bio_worker_has_independent_batch():
    from workers.tennis_player_bio_backfill_worker import _run_once
    with patch(
        "workers.tennis_player_bio_backfill_worker.fill_player_bio",
        return_value={"resolved": 3},
    ) as fill:
        assert _run_once() == {"resolved": 3}
    fill.assert_called_once_with(limit=40)


def test_tournament_worker_isolates_each_substep():
    from workers.tennis_tournament_metadata_backfill_worker import _run_once
    mod = "workers.tennis_tournament_metadata_backfill_worker"
    with patch(f"{mod}.fill_tournament_levels", side_effect=RuntimeError("bad")), patch(
        f"{mod}.refine_generic_levels", return_value={"refined": 2},
    ), patch(f"{mod}.fill_tournament_locations", return_value={"updated": 4}):
        result = _run_once()
    assert result["levels"] == {"error": "RuntimeError: bad"}
    assert result["levels_refined"] == {"refined": 2}
    assert result["locations"] == {"updated": 4}


def test_elo_worker_runs_full_backfill():
    from workers.tennis_self_elo_backfill_worker import _run_once
    with patch(
        "workers.tennis_self_elo_backfill_worker.run_elo_backfill",
        return_value={"total_processed": 10},
    ) as elo, patch(
        "workers.tennis_self_elo_backfill_worker.run_weighted_elo_backfill",
        return_value={"ATP": {"processed": 10}},
    ) as weighted:
        assert _run_once() == {"total_processed": 10, "weighted": {"ATP": {"processed": 10}}}
    elo.assert_called_once_with()
    weighted.assert_called_once_with()
