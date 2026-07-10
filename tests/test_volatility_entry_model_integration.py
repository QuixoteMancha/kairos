"""Tests for volatility_entry's dwell-time-model sigma integration
(estimate_sigma / _estimate_sigma_from_dwell_time). Mirrors the patching
style already used in test_limit_order_formation.py for the same registry."""
from unittest.mock import MagicMock, patch

from src.core.strategies.event_volatility.base import DwellBand
from src.core.strategies import volatility_entry as ve


def _fake_model(bands):
    m = MagicMock()
    m.estimate.return_value = bands
    return m


def test_model_priors_are_used_when_valid():
    # Wide-ish bands (logit(0.65)-logit(0.35) ~= 1.24) weighted 1.0 -> well
    # within [_SIGMA_MIN, _SIGMA_MAX], so the clamp is a no-op here.
    bands = [DwellBand(low=0.35, high=0.65, side="team_a", expected_time_fraction=1.0)]
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model(bands)):
        sigma = ve.estimate_sigma("football", {"team_a": "X", "team_b": "Y"})

    assert sigma is not None
    assert ve._SIGMA_MIN <= sigma <= ve._SIGMA_MAX


def test_unsupported_category_falls_back_to_none():
    # Baseball (uncalibrated) and weather (non-probability bands) are
    # deliberately excluded regardless of what the registry would return.
    bands = [DwellBand(low=0.35, high=0.65, side="team_a", expected_time_fraction=1.0)]
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model(bands)):
        assert ve.estimate_sigma("baseball", {}) is None
        assert ve.estimate_sigma("weather", {}) is None


def test_no_registered_model_falls_back_to_none():
    with patch("src.core.strategies.volatility_entry.get_model", return_value=None):
        assert ve.estimate_sigma("football", {}) is None


def test_model_error_falls_back_to_none():
    broken = MagicMock()
    broken.estimate.side_effect = RuntimeError("boom")
    with patch("src.core.strategies.volatility_entry.get_model", return_value=broken):
        assert ve.estimate_sigma("football", {}) is None


def test_empty_bands_fall_back_to_none():
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model([])):
        assert ve.estimate_sigma("football", {}) is None


def test_zero_total_dwell_fraction_falls_back_to_none():
    bands = [DwellBand(low=0.35, high=0.65, side="team_a", expected_time_fraction=0.0)]
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model(bands)):
        assert ve.estimate_sigma("football", {}) is None


def test_narrow_band_is_clamped_to_sigma_min():
    # A degenerate/very tight band should clamp to _SIGMA_MIN, never below it.
    bands = [DwellBand(low=0.499, high=0.501, side="team_a", expected_time_fraction=1.0)]
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model(bands)):
        sigma = ve.estimate_sigma("football", {})

    assert sigma == ve._SIGMA_MIN


def test_wide_band_is_clamped_to_sigma_max():
    # A band nearly spanning [0,1] should clamp to _SIGMA_MAX, never above it.
    bands = [DwellBand(low=0.001, high=0.999, side="team_a", expected_time_fraction=1.0)]
    with patch("src.core.strategies.volatility_entry.get_model", return_value=_fake_model(bands)):
        sigma = ve.estimate_sigma("football", {})

    assert sigma == ve._SIGMA_MAX