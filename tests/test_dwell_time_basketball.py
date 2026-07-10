from src.core.strategies.event_volatility.basketball_winprob import BasketballWinProbModel


def _top_band_fraction(bands, side):
    top = [b for b in bands if b.side == side and b.low >= 0.80]
    return sum(b.expected_time_fraction for b in top)


def test_lopsided_matchup_favorite_dwells_in_high_bands():
    model = BasketballWinProbModel()
    bands = model.estimate({
        "team_a": "Strong", "team_b": "Weak",
        "team_a_rating": 1800.0, "team_b_rating": 1300.0,
        "n_sims": 60, "seed": 42,
    })
    assert _top_band_fraction(bands, "team_a") > _top_band_fraction(bands, "team_b")


def test_even_matchup_centers_near_fifty_fifty():
    model = BasketballWinProbModel()
    bands = model.estimate({
        "team_a": "A", "team_b": "B",
        "team_a_rating": 1500.0, "team_b_rating": 1500.0,
        "n_sims": 60, "seed": 7,
    })
    mid_a = sum(b.expected_time_fraction for b in bands if b.side == "team_a" and 0.40 <= b.low < 0.60)
    high_a = _top_band_fraction(bands, "team_a")
    assert mid_a > high_a


def test_deterministic_with_fixed_seed():
    model = BasketballWinProbModel()
    ctx = {"team_a": "A", "team_b": "B", "team_a_rating": 1600.0, "team_b_rating": 1450.0,
           "n_sims": 30, "seed": 123}
    bands1 = model.estimate(dict(ctx))
    bands2 = model.estimate(dict(ctx))
    assert bands1 == bands2


def test_bands_sum_to_one_per_side():
    model = BasketballWinProbModel()
    bands = model.estimate({
        "team_a": "A", "team_b": "B", "team_a_rating": 1550.0, "team_b_rating": 1500.0,
        "n_sims": 40, "seed": 1,
    })
    for side in ("team_a", "team_b"):
        total = sum(b.expected_time_fraction for b in bands if b.side == side)
        assert abs(total - 1.0) < 1e-6
