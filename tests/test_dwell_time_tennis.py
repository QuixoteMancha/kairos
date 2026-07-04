"""Tests for the tennis Markov dwell-time model (Part 2 of the dwell-time framework)."""
from __future__ import annotations

from src.core.strategies.dwell_time.tennis_markov import (
    TennisMarkovDwellTimeModel,
    calibrate_serve_pct_by_bucket,
    _bucket_for_rank,
)


def _synthetic_rows() -> list[dict]:
    """A handful of synthetic Sackmann-shaped rows: top players serve better."""
    rows = []
    # Top-10 players: ~65% serve points won.
    for i in range(20):
        rows.append({
            "winner_rank": "5", "w_svpt": "80", "w_1stWon": "34", "w_2ndWon": "18",
            "loser_rank": "150", "l_svpt": "78", "l_1stWon": "28", "l_2ndWon": "14",
        })
    # Mid-pack (top 50-100): ~58%.
    for i in range(20):
        rows.append({
            "winner_rank": "60", "w_svpt": "82", "w_1stWon": "30", "w_2ndWon": "17",
            "loser_rank": "90", "l_svpt": "80", "l_1stWon": "28", "l_2ndWon": "16",
        })
    return rows


def test_calibrate_serve_pct_by_bucket_from_synthetic_rows():
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    top10_bucket = _bucket_for_rank(5)
    mid_bucket = _bucket_for_rank(60)
    assert top10_bucket in calibration
    assert mid_bucket in calibration
    # Top-10 players should calibrate to a higher serve-win% than 51-100 bucket.
    assert calibration[top10_bucket] > calibration[mid_bucket]
    assert 0.0 < calibration[top10_bucket] < 1.0


def test_calibrate_ignores_invalid_rows():
    rows = [
        {"winner_rank": "abc", "w_svpt": "80", "w_1stWon": "30", "w_2ndWon": "10",
         "loser_rank": "50", "l_svpt": "0", "l_1stWon": "0", "l_2ndWon": "0"},
        {},  # missing everything
    ]
    calibration = calibrate_serve_pct_by_bucket(rows=rows)
    # Should not raise, and buckets fall back to the default when no data.
    assert all(0.0 < v < 1.0 for v in calibration.values())


def test_lopsided_matchup_favors_top_player_heavily():
    """Top-5 vs top-200: player_a's dwell-time should concentrate in high bands."""
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)
    bands = model.estimate({"player_a_rank": 5, "player_b_rank": 200, "n_matches": 60})

    a_bands = [b for b in bands if b.side == "player_a"]
    assert a_bands, "expected player_a bands"
    high_band_time = sum(b.expected_time_fraction for b in a_bands if b.low >= 0.7)
    low_band_time = sum(b.expected_time_fraction for b in a_bands if b.high <= 0.3)
    assert high_band_time > low_band_time
    assert high_band_time > 0.4  # heavily favored player should dwell mostly high


def test_even_matchup_is_more_balanced():
    """Top-10 vs top-12 (same bucket): distribution should center near 50%."""
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)
    bands = model.estimate({"player_a_rank": 10, "player_b_rank": 12, "n_matches": 60})

    a_bands = [b for b in bands if b.side == "player_a"]
    mid_band_time = sum(b.expected_time_fraction for b in a_bands if 0.3 <= b.low < 0.7)
    extreme_band_time = sum(b.expected_time_fraction for b in a_bands if b.low < 0.1 or b.high > 0.9)
    assert mid_band_time > extreme_band_time


def test_lopsided_matchup_dwells_more_extreme_than_even_matchup():
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)

    lopsided = model.estimate({"player_a_rank": 5, "player_b_rank": 200, "n_matches": 60})
    even = model.estimate({"player_a_rank": 10, "player_b_rank": 12, "n_matches": 60})

    lopsided_a = [b for b in lopsided if b.side == "player_a"]
    even_a = [b for b in even if b.side == "player_a"]

    lopsided_extreme = sum(b.expected_time_fraction for b in lopsided_a if b.low < 0.2 or b.high > 0.8)
    even_extreme = sum(b.expected_time_fraction for b in even_a if b.low < 0.2 or b.high > 0.8)
    assert lopsided_extreme > even_extreme


def test_bands_sum_to_approximately_one_per_side():
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)
    bands = model.estimate({"player_a_rank": 20, "player_b_rank": 70, "n_matches": 60})

    for side in ("player_a", "player_b"):
        total = sum(b.expected_time_fraction for b in bands if b.side == side)
        assert abs(total - 1.0) < 0.05, f"{side} bands sum to {total}, expected ~1.0"


def test_estimate_requires_ranks_in_context():
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket={_bucket_for_rank(1): 0.62})
    try:
        model.estimate({})
        assert False, "expected ValueError for missing context keys"
    except ValueError:
        pass
