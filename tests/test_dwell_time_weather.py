"""Tests for the weather climatology dwell-time model (mocked HTTP, no real network)."""
from datetime import date
from unittest.mock import patch

from src.core.strategies.dwell_time.weather_climatology import WeatherClimatologyModel


def _mock_fetch_centered(center: float, spread: float = 1.0, n_years: int = 15):
    """Builds a fake _fetch_year_window that always returns a small sample
    centered on `center` regardless of the requested date range."""
    def _fetch(latitude, longitude, start, end):
        # A few points per call, symmetric around `center`.
        return [center - spread, center, center, center + spread]
    return _fetch


def test_bands_concentrate_around_historical_mean():
    with patch(
        "src.core.strategies.dwell_time.weather_climatology._fetch_year_window",
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
        "src.core.strategies.dwell_time.weather_climatology._fetch_year_window",
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
        "src.core.strategies.dwell_time.weather_climatology._fetch_year_window",
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
        "src.core.strategies.dwell_time.weather_climatology._fetch_year_window",
        return_value=[],
    ):
        model = WeatherClimatologyModel()
        bands = model.estimate({
            "latitude": 0.0, "longitude": 0.0,
            "target_date": "2026-07-15", "now": "2026-01-01",
        })

    assert bands == []
