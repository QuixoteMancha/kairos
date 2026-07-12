"""Tests for the weather climatology dwell-time model (mocked HTTP, no real network)."""
from datetime import date
from unittest.mock import patch

from src.core.strategies.event_volatility.weather_climatology import WeatherClimatologyModel


def _mock_fetch_centered(center: float, spread: float = 1.0, n_years: int = 15):
    """Builds a fake _fetch_year_window that always returns a small sample
    centered on `center` regardless of the requested date range."""
    def _fetch(latitude, longitude, start, end):
        # A few points per call, symmetric around `center`.
        return [center - spread, center, center, center + spread]
    return _fetch


def test_bands_concentrate_around_historical_mean():
    with patch(
        "src.core.strategies.event_volatility.weather_climatology._fetch_year_window",
        side_effect=_mock_fetch_centered(20.0, spread=1.0),
    ):
        model = WeatherClimatologyModel()
        bands = model.estimate({
            "latitude": 41.0082, "longitude": 28.9784,
            "target_date": "2026-07-15", "now": "2026-01-01",
            "band_width_c": 1.0,
        })

    assert bands
    best = max(bands, key=lambda b: b.expected_time_fraction)
    assert best.low <= 20.0 < best.high or abs(best.low - 20.0) <= 1.0


def test_probabilities_sum_to_one():
    with patch(
        "src.core.strategies.event_volatility.weather_climatology._fetch_year_window",
        side_effect=_mock_fetch_centered(15.0, spread=2.0),
    ):
        model = WeatherClimatologyModel()
        bands = model.estimate({
            "latitude": 51.5, "longitude": -0.1,
            "target_date": "2026-09-01", "now": "2026-06-01",
            "band_width_c": 0.5,
        })

    total = sum(b.expected_time_fraction for b in bands)
    assert 0.95 <= total <= 1.05


def test_uncertainty_narrows_with_shorter_lead_time():
    with patch(
        "src.core.strategies.event_volatility.weather_climatology._fetch_year_window",
        side_effect=_mock_fetch_centered(25.0, spread=3.0),
    ):
        model = WeatherClimatologyModel()
        far_bands = model.estimate({
            "latitude": 25.7, "longitude": -80.2,
            "target_date": "2026-12-01", "now": "2026-01-01",  # ~330 days out
            "band_width_c": 1.0,
        })
        near_bands = model.estimate({
            "latitude": 25.7, "longitude": -80.2,
            "target_date": "2026-12-01", "now": "2026-11-29",  # 2 days out
            "band_width_c": 1.0,
        })

    far_peak = max(b.expected_time_fraction for b in far_bands)
    near_peak = max(b.expected_time_fraction for b in near_bands)
    # Tighter distribution -> higher peak probability mass in a single bin.
    assert near_peak > far_peak


def test_empty_sample_returns_no_bands():
    with patch(
        "src.core.strategies.event_volatility.weather_climatology._fetch_year_window",
        return_value=[],
    ):
        model = WeatherClimatologyModel()
        bands = model.estimate({
            "latitude": 0.0, "longitude": 0.0,
            "target_date": "2026-07-15", "now": "2026-01-01",
        })

    assert bands == []


def test_fair_prob_for_strike_partial_and_full_bins():
    from src.core.strategies.event_volatility.weather_climatology import fair_prob_for_strike
    from src.core.strategies.event_volatility.base import DwellBand

    bands = [
        DwellBand(low=18.0, high=19.0, side="18.0-19.0", expected_time_fraction=0.3),
        DwellBand(low=19.0, high=20.0, side="19.0-20.0", expected_time_fraction=0.2),
        DwellBand(low=20.0, high=21.0, side="20.0-21.0", expected_time_fraction=0.5),
    ]
    # Strike covers the first two bins fully (18.0-20.0), the third not at all.
    result = fair_prob_for_strike(bands, strike_low=18.0, strike_high=20.0)
    assert abs(result - 0.5) < 1e-9

    # Strike covers all three bins.
    result_all = fair_prob_for_strike(bands, strike_low=18.0, strike_high=21.0)
    assert abs(result_all - 1.0) < 1e-9


def test_fair_prob_for_strike_empty_bands_raises():
    from src.core.strategies.event_volatility.weather_climatology import fair_prob_for_strike
    import pytest

    with pytest.raises(ValueError):
        fair_prob_for_strike([], strike_low=18.0, strike_high=20.0)


def test_fair_prob_for_strike_invalid_range_raises():
    from src.core.strategies.event_volatility.weather_climatology import fair_prob_for_strike
    from src.core.strategies.event_volatility.base import DwellBand
    import pytest

    bands = [DwellBand(low=18.0, high=19.0, side="x", expected_time_fraction=1.0)]
    with pytest.raises(ValueError):
        fair_prob_for_strike(bands, strike_low=20.0, strike_high=18.0)
