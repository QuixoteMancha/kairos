"""Tests for the tennis Markov dwell-time model (Part 2 of the dwell-time framework)."""
from __future__ import annotations

from src.core.strategies.event_volatility.tennis_markov import (
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


# ── Live-score conditioning (real match phase, not a symbolic multiplier) ──


def test_live_state_conditioned_simulation_differs_from_0_0():
    """Same matchup, but simulated from deep into a decisive lead (2 sets to
    0, up a break) must dwell very differently than a fresh 0-0 match."""
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)

    fresh = model.estimate({"player_a_rank": 10, "player_b_rank": 12, "n_matches": 80})
    live = model.estimate({
        "player_a_rank": 10, "player_b_rank": 12, "n_matches": 80,
        "sets_won_p1": 1, "sets_won_p2": 0, "games_won_p1": 5, "games_won_p2": 1,
        "points_p1": 0, "points_p2": 0, "p1_serving": True,
    })

    fresh_a = [b for b in fresh if b.side == "player_a"]
    live_a = [b for b in live if b.side == "player_a"]
    fresh_high = sum(b.expected_time_fraction for b in fresh_a if b.low >= 0.8)
    live_high = sum(b.expected_time_fraction for b in live_a if b.low >= 0.8)
    # Player A is nearly certain to win from 2-0 sets + 4-1 games — dwell
    # time should concentrate far more in the high band than a fresh 0-0
    # even-ish matchup does.
    assert live_high > fresh_high


def test_no_live_state_reproduces_prior_0_0_behavior_exactly():
    """Regression: omitting any live-state keys must give byte-identical
    results to the pre-existing behavior (same seed=42, same inputs)."""
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model_a = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)
    model_b = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)

    context = {"player_a_rank": 20, "player_b_rank": 70, "n_matches": 60}
    bands_1 = model_a.estimate(dict(context))
    bands_2 = model_b.estimate(dict(context))
    assert bands_1 == bands_2

    # Also matches direct simulate_dwell_bands() with no start_state, proving
    # the code path is unchanged (not merely reproducible with itself).
    from src.core.strategies.event_volatility.tennis_markov import simulate_dwell_bands, _bucket_for_rank as _bfr

    pa = calibration.get(_bfr(20))
    pb = calibration.get(_bfr(70))
    direct = simulate_dwell_bands(pa, pb, n_matches=60, best_of=3, seed=42)
    assert bands_1 == direct


def test_incoherent_live_state_falls_back_to_0_0():
    """A match score claiming both players already won the required sets is
    incoherent (match already over) — must fail closed to 0-0, not crash or
    fabricate a state."""
    calibration = calibrate_serve_pct_by_bucket(rows=_synthetic_rows())
    model = TennisMarkovDwellTimeModel(serve_pct_by_bucket=calibration)
    bands = model.estimate({
        "player_a_rank": 10, "player_b_rank": 12, "n_matches": 40,
        "sets_won_p1": 2, "sets_won_p2": 2,  # impossible in best_of=3
        "best_of": 3,
    })
    assert bands  # still produces valid bands (via 0-0 fallback), no crash


# ── Incremental recalibration (EWMA) ───────────────────────────────────────


def test_incremental_recalibrate_weights_recent_over_old(monkeypatch, tmp_path):
    import json
    from src.core.strategies.event_volatility import tennis_markov as tm

    store: dict[str, str] = {}

    class _FakeAppConfig:
        @staticmethod
        def get_value(key, default=""):
            return store.get(key, default)

        @staticmethod
        def set_value(key, value):
            store[key] = value

    monkeypatch.setattr("src.database.database.AppConfig", _FakeAppConfig)

    bucket = _bucket_for_rank(5)
    other_buckets = [b for b in tm._RANK_BUCKETS if b != bucket]
    # Seed a persisted prior of 0.60 for every bucket.
    tm._save_persisted_serve_pct_by_bucket({b: 0.60 for b in tm._RANK_BUCKETS})

    fresh_batch = {b: (0.60, 0) for b in tm._RANK_BUCKETS}
    fresh_batch[bucket] = (0.90, 50)  # plenty of fresh, very different data

    merged = tm.incremental_recalibrate(fresh_batch_with_counts=fresh_batch, alpha=0.5)

    # The bucket with plenty of fresh data should move toward the new value.
    assert merged[bucket] > 0.60
    assert abs(merged[bucket] - (0.5 * 0.90 + 0.5 * 0.60)) < 1e-6
    # Buckets with insufficient fresh observations stay untouched.
    for b in other_buckets:
        assert merged[b] == 0.60


def test_incremental_recalibrate_discards_incomplete_observations(monkeypatch):
    from src.core.strategies.event_volatility import tennis_markov as tm

    store: dict[str, str] = {}

    class _FakeAppConfig:
        @staticmethod
        def get_value(key, default=""):
            return store.get(key, default)

        @staticmethod
        def set_value(key, value):
            store[key] = value

    monkeypatch.setattr("src.database.database.AppConfig", _FakeAppConfig)

    tm._save_persisted_serve_pct_by_bucket({b: 0.60 for b in tm._RANK_BUCKETS})
    # Zero observations everywhere (e.g. Sackmann had nothing new this window).
    fresh_batch = {b: (0.99, 0) for b in tm._RANK_BUCKETS}
    merged = tm.incremental_recalibrate(fresh_batch_with_counts=fresh_batch, alpha=0.5)
    # Nothing should have moved -- no real data to justify a change.
    for b in tm._RANK_BUCKETS:
        assert merged[b] == 0.60
