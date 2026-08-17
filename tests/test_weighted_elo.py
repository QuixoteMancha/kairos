from models.volatility.sports.tennis.weighted_elo import (
    calibrate, combined_probability, fit_platt, margin_multiplier,
    regress_for_inactivity, update_pair,
)
from scripts.tennis.compute_weighted_elo import normalize_surface, score_set_margin


def test_inactivity_regresses_halfway_to_mean_after_one_year():
    assert regress_for_inactivity(1700.0, 365) == 1600.0


def test_surface_and_overall_are_blended_equally():
    probability = combined_probability(1600, 1500, 1500, 1500)
    assert 0.5 < probability < 0.65


def test_margin_increases_rating_update_but_is_capped():
    small = update_pair(1500, 1500, True, 1)[0]
    large = update_pair(1500, 1500, True, 3)[0]
    assert large > small
    assert margin_multiplier(100) == 1.5


def test_score_margin_and_surface_normalization():
    assert score_set_margin("6-4, 3-6, 6-2") == 1
    assert score_set_margin("6-4, 6-3") == 2
    assert normalize_surface("Hard indoor") == "Hard"


def test_platt_calibration_is_identity_with_small_sample():
    assert fit_platt([(0.7, True)] * 20) == (0.0, 1.0)
    assert abs(calibrate(0.7, 0.0, 1.0) - 0.7) < 1e-9


def test_platt_on_symmetric_observations_has_near_zero_intercept():
    observations = [(0.7, True), (0.3, False)] * 100
    intercept, slope = fit_platt(observations)
    assert abs(intercept) < 1e-9
    assert slope > 1.0
