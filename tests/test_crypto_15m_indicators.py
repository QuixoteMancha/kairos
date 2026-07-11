"""Tests for src.core.strategies.event_volatility.crypto_15m.

Fixtures use synthetic OHLC series with hand-verifiable results (no
production data, no network, no DB) per the task's testing requirement.
"""

from __future__ import annotations

import math

import pytest

from src.core.strategies.event_volatility.crypto_15m import (
    OhlcBar,
    atr,
    bars_from_snapshots,
    momentum_bucket,
    momentum_zscore,
    rolling_return_std,
    seconds_to_close,
    stochastic_oscillator,
    time_fraction_bucket,
    true_ranges,
    calibrate_crypto_momentum_correlation,
    estimate_crossing_probability,
    CalibratedLookup,
    MIN_SAMPLE_FOR_CALIBRATION,
)


def _constant_tr_bars(n: int, start_close: float = 100.0) -> list[OhlcBar]:
    """Each bar: high = close+1, low = close-1, close increases by 1 per
    bar. True Range for every bar (including the first) works out to
    exactly 2 -- verified by hand:
      bar0: high-low = 2 (no prev close).
      bar_i (i>0): high-low = 2; |high - prev_close| = |(c+1) - (c-1)| = 2;
                   |low - prev_close| = |(c-1) - (c-1)| = 0. max = 2.
    """
    bars = []
    close = start_close
    for _ in range(n):
        bars.append(OhlcBar(high=close + 1, low=close - 1, close=close))
        close += 1
    return bars


def test_true_ranges_first_bar_is_high_minus_low():
    bars = _constant_tr_bars(3)
    trs = true_ranges(bars)
    assert trs == [2.0, 2.0, 2.0]


def test_atr_constant_true_range():
    bars = _constant_tr_bars(20)
    result = atr(bars, window=14)
    assert result == pytest.approx(2.0)


def test_atr_insufficient_bars_returns_none():
    bars = _constant_tr_bars(5)
    assert atr(bars, window=14) is None


def test_stochastic_known_values():
    # 3 bars, window=3, smooth=1 (no extra smoothing) so %D == %K.
    # bar0: low=10, high=20
    # bar1: low=15, high=25
    # bar2: low=18, high=22, close=21
    # highest_high(window=3) = 25, lowest_low(window=3) = 10
    # %K = 100 * (21 - 10) / (25 - 10) = 100 * 11/15 = 73.333...
    bars = [
        OhlcBar(high=20, low=10, close=15),
        OhlcBar(high=25, low=15, close=20),
        OhlcBar(high=22, low=18, close=21),
    ]
    result = stochastic_oscillator(bars, window=3, smooth=1)
    assert result is not None
    k, d = result
    assert k == pytest.approx(100 * 11 / 15)
    assert d == pytest.approx(k)  # smooth=1 -> %D is just the last %K.


def test_stochastic_flat_range_returns_midpoint():
    # highest_high == lowest_low (flat market) -> defined as 50.0, not a
    # division by zero / NaN.
    bars = [OhlcBar(high=10, low=10, close=10) for _ in range(3)]
    k, d = stochastic_oscillator(bars, window=3, smooth=1)
    assert k == 50.0
    assert d == 50.0


def test_stochastic_insufficient_bars_returns_none():
    bars = [OhlcBar(high=20, low=10, close=15), OhlcBar(high=25, low=15, close=20)]
    assert stochastic_oscillator(bars, window=3, smooth=1) is None


def test_rolling_return_std_zero_for_constant_ratio():
    # Closes growing by a constant multiplicative factor -> constant
    # log-return every step -> std of returns is exactly 0.
    closes = [100.0 * (1.01 ** i) for i in range(16)]
    bars = [OhlcBar(high=c, low=c, close=c) for c in closes]
    result = rolling_return_std(bars, window=14, log_returns=True)
    assert result == pytest.approx(0.0, abs=1e-9)


def test_rolling_return_std_nonzero_for_varying_returns():
    closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93]
    bars = [OhlcBar(high=c, low=c, close=c) for c in closes]
    result = rolling_return_std(bars, window=14, log_returns=True)
    assert result is not None
    assert result > 0.0


def test_rolling_return_std_insufficient_bars_returns_none():
    bars = _constant_tr_bars(5)
    assert rolling_return_std(bars, window=14) is None


def test_rolling_return_std_fails_closed_on_nonpositive_price():
    bars = [OhlcBar(high=c, low=c, close=c) for c in [1, 2, 0, 3]]
    assert rolling_return_std(bars, window=2) is None


def test_momentum_zscore_known_value():
    # ATR is constant 2.0 (see _constant_tr_bars). Close moved from bar[-6]
    # to bar[-1] (lookback=5): delta = 5 (closes increase by 1/bar).
    # momentum_zscore = 5 / 2.0 = 2.5
    bars = _constant_tr_bars(20)
    result = momentum_zscore(bars, lookback=5, atr_window=14)
    assert result == pytest.approx(2.5)


def test_momentum_zscore_insufficient_bars_returns_none():
    bars = _constant_tr_bars(3)
    assert momentum_zscore(bars, lookback=5, atr_window=14) is None


def test_bars_from_snapshots_adapts_shape():
    class FakeSnapshot:
        def __init__(self, h, l, c):
            self.high, self.low, self.close = h, l, c

    snaps = [FakeSnapshot(11, 9, 10), FakeSnapshot(12, 10, 11)]
    bars = bars_from_snapshots(snaps)
    assert bars == [OhlcBar(11, 9, 10), OhlcBar(12, 10, 11)]


# ── seconds_to_close ────────────────────────────────────────────────────

def test_seconds_to_close_basic():
    import datetime

    now = datetime.datetime(2026, 7, 11, 12, 0, 0)
    close = datetime.datetime(2026, 7, 11, 12, 10, 0)
    assert seconds_to_close(now, close) == pytest.approx(600.0)


def test_seconds_to_close_none_when_missing():
    assert seconds_to_close(None, None) is None


def test_seconds_to_close_fails_closed_when_already_past():
    import datetime

    now = datetime.datetime(2026, 7, 11, 12, 10, 0)
    close = datetime.datetime(2026, 7, 11, 12, 0, 0)
    assert seconds_to_close(now, close) is None


# ── Bucketing ────────────────────────────────────────────────────────────

def test_momentum_bucket_symmetric():
    assert momentum_bucket(-5.0) == 0
    assert momentum_bucket(0.0) == 4
    assert momentum_bucket(5.0) == 7


def test_time_fraction_bucket_edges():
    assert time_fraction_bucket(30, market_duration_seconds=900) == 0  # 30/900=0.033 < 0.1
    assert time_fraction_bucket(850, market_duration_seconds=900) == 4  # 0.944 >= 0.75


# ── Calibration + fail-closed gate ──────────────────────────────────────

def test_calibrate_from_synthetic_rows_deterministic():
    rows = []
    # 40 identical observations in the same cell, half crossed.
    for i in range(40):
        rows.append({
            "momentum_zscore": 0.2,   # bucket index 3 (0 <= 0.2 < 0.5)
            "seconds_to_close": 100.0,  # fraction 100/900=0.111 -> bucket 1 (0.1<=x<0.25)
            "crossed_50": i % 2 == 0,
        })
    lookup = calibrate_crypto_momentum_correlation(rows)
    cell = (momentum_bucket(0.2), time_fraction_bucket(100.0))
    assert lookup.counts[cell] == 40
    assert lookup.probabilities[cell] == pytest.approx(0.5)


def test_calibrate_ignores_malformed_rows():
    rows = [
        {"momentum_zscore": "not-a-number", "seconds_to_close": 100.0, "crossed_50": True},
        {"momentum_zscore": 0.2, "seconds_to_close": -5.0, "crossed_50": True},  # negative -> skipped
        {"momentum_zscore": 0.2, "seconds_to_close": 100.0, "crossed_50": True},
    ]
    lookup = calibrate_crypto_momentum_correlation(rows)
    assert sum(lookup.counts.values()) == 1


def test_estimate_crossing_probability_no_lookup_is_not_calibrated():
    result = estimate_crossing_probability(0.2, 100.0, lookup=None)
    assert result.calibrated is False
    assert result.probability is None
    assert result.sample_count == 0


def test_estimate_crossing_probability_below_min_sample_is_not_calibrated():
    rows = [
        {"momentum_zscore": 0.2, "seconds_to_close": 100.0, "crossed_50": True}
        for _ in range(MIN_SAMPLE_FOR_CALIBRATION - 1)
    ]
    lookup = calibrate_crypto_momentum_correlation(rows)
    result = estimate_crossing_probability(0.2, 100.0, lookup=lookup)
    assert result.calibrated is False
    assert result.probability is None
    assert result.sample_count == MIN_SAMPLE_FOR_CALIBRATION - 1


def test_estimate_crossing_probability_calibrated_when_sample_sufficient():
    rows = []
    for i in range(MIN_SAMPLE_FOR_CALIBRATION):
        rows.append({
            "momentum_zscore": 0.2,
            "seconds_to_close": 100.0,
            "crossed_50": i < (MIN_SAMPLE_FOR_CALIBRATION * 3 // 4),  # ~75% crossed
        })
    lookup = calibrate_crypto_momentum_correlation(rows)
    result = estimate_crossing_probability(0.2, 100.0, lookup=lookup)
    assert result.calibrated is True
    assert result.sample_count == MIN_SAMPLE_FOR_CALIBRATION
    expected = (MIN_SAMPLE_FOR_CALIBRATION * 3 // 4) / MIN_SAMPLE_FOR_CALIBRATION
    assert result.probability == pytest.approx(expected, abs=1e-6)


def test_estimate_crossing_probability_per_cell_not_global():
    # Cell A well sampled, cell B (different momentum bucket) sparse --
    # must independently fail-closed for B even though the lookup overall
    # has plenty of total rows.
    rows = [
        {"momentum_zscore": 0.2, "seconds_to_close": 100.0, "crossed_50": True}
        for _ in range(MIN_SAMPLE_FOR_CALIBRATION * 3)
    ]
    rows.append({"momentum_zscore": 3.0, "seconds_to_close": 100.0, "crossed_50": True})
    lookup = calibrate_crypto_momentum_correlation(rows)

    result_a = estimate_crossing_probability(0.2, 100.0, lookup=lookup)
    result_b = estimate_crossing_probability(3.0, 100.0, lookup=lookup)
    assert result_a.calibrated is True
    assert result_b.calibrated is False
    assert result_b.sample_count == 1
