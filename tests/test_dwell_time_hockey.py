from src.core.strategies.event_volatility.hockey_winprob import HockeyWinProbModel


def _top_band_fraction(bands, side):
    top = [b for b in bands if b.side == side and b.low >= 0.80]
    return sum(b.expected_time_fraction for b in top)


def test_lopsided_matchup_favorite_dwells_in_high_bands():
    model = HockeyWinProbModel()
    bands = model.estimate({
        "team_a": "Strong", "team_b": "Weak",
        "team_a_rating": 1800.0, "team_b_rating": 1300.0,
        "n_sims": 60, "seed": 42,
    })
    assert _top_band_fraction(bands, "team_a") > _top_band_fraction(bands, "team_b")


def test_bands_sum_to_one_per_side():
    model = HockeyWinProbModel()
    bands = model.estimate({
        "team_a": "A", "team_b": "B", "team_a_rating": 1550.0, "team_b_rating": 1500.0,
        "n_sims": 40, "seed": 1,
    })
    for side in ("team_a", "team_b"):
        total = sum(b.expected_time_fraction for b in bands if b.side == side)
        assert abs(total - 1.0) < 1e-6


def test_deterministic_with_fixed_seed():
    model = HockeyWinProbModel()
    ctx = {"team_a": "A", "team_b": "B", "team_a_rating": 1600.0, "team_b_rating": 1450.0,
           "n_sims": 30, "seed": 123}
    bands1 = model.estimate(dict(ctx))
    bands2 = model.estimate(dict(ctx))
    assert bands1 == bands2


def test_pre_game_win_prob_matches_elo_win_prob_directly():
    from src.core.strategies.event_volatility.hockey_winprob import (
        pre_game_win_prob,
        _elo_win_prob,
    )

    assert pre_game_win_prob(1800.0, 1500.0) == _elo_win_prob(1800.0, 1500.0)
    assert pre_game_win_prob(1500.0, 1500.0) == _elo_win_prob(1500.0, 1500.0)


def test_model_registered_under_ice_hockey_key():
    from src.core.strategies.event_volatility import get_model

    assert isinstance(get_model("ice_hockey"), HockeyWinProbModel)


def test_ice_hockey_excluded_from_dynamic_epsilon_uncalibrated():
    """Same fail-closed status as baseball -- not empirically calibrated,
    must never feed limit-order level placement."""
    from src.core.strategies.limit_order_formation import _NO_DYNAMIC_EPSILON_CATEGORIES

    assert "ice_hockey" in _NO_DYNAMIC_EPSILON_CATEGORIES
