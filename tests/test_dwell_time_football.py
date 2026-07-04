from src.core.strategies.dwell_time.football_poisson import FootballPoissonDwellTimeModel


def _fraction_above(bands, side, threshold):
    return sum(b.expected_time_fraction for b in bands if b.side == side and b.low >= threshold)


def test_big_favorite_dwells_mostly_in_high_bands():
    model = FootballPoissonDwellTimeModel(n_simulations=300, seed=42)
    bands = model.estimate({"team_a": "Favorite", "team_b": "Underdog",
                             "team_a_elo": 2100.0, "team_b_elo": 1500.0})
    fav_high = _fraction_above(bands, "Favorite", 0.7)
    dog_high = _fraction_above(bands, "Underdog", 0.7)
    assert fav_high > 0.5
    assert dog_high < 0.1


def test_even_match_centers_around_fifty_fifty():
    model = FootballPoissonDwellTimeModel(n_simulations=300, seed=42)
    bands = model.estimate({"team_a": "Even A", "team_b": "Even B",
                             "team_a_elo": 1700.0, "team_b_elo": 1700.0})
    a_extreme = _fraction_above(bands, "Even A", 0.8)
    b_extreme = _fraction_above(bands, "Even B", 0.8)
    # Even match (with home advantage baked in) should rarely dwell in the
    # most extreme bands compared to a lopsided matchup.
    assert a_extreme < 0.3
    assert b_extreme < 0.3


def test_deterministic_with_fixed_seed():
    m1 = FootballPoissonDwellTimeModel(n_simulations=100, seed=7)
    m2 = FootballPoissonDwellTimeModel(n_simulations=100, seed=7)
    ctx = {"team_a": "X", "team_b": "Y", "team_a_elo": 1800.0, "team_b_elo": 1600.0}
    b1 = m1.estimate(ctx)
    b2 = m2.estimate(ctx)
    assert [b.expected_time_fraction for b in b1] == [b.expected_time_fraction for b in b2]


def test_bands_sum_to_one_per_side():
    model = FootballPoissonDwellTimeModel(n_simulations=200, seed=1)
    bands = model.estimate({"team_a": "A", "team_b": "B", "team_a_elo": 1900.0, "team_b_elo": 1650.0})
    total_a = sum(b.expected_time_fraction for b in bands if b.side == "A")
    total_b = sum(b.expected_time_fraction for b in bands if b.side == "B")
    assert abs(total_a - 1.0) < 0.01
    assert abs(total_b - 1.0) < 0.01
