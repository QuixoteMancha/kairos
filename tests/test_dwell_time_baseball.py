from src.core.strategies.dwell_time.baseball_winexp import BaseballWinExpModel


def _top_band_fraction(bands, side):
    top = [b for b in bands if b.side == side and b.low >= 0.80]
    return sum(b.expected_time_fraction for b in top)


def test_lopsided_matchup_favorite_dwells_in_high_bands():
    model = BaseballWinExpModel()
    bands = model.estimate({
        "team_a": "Strong", "team_b": "Weak",
        "team_a_rating": 1750.0, "team_b_rating": 1300.0,
        "n_sims": 25,
        "seed": 42,
    })
    assert _top_band_fraction(bands, "team_a") > _top_band_fraction(bands, "team_b")


def test_even_matchup_not_dominated_by_one_side():
    model = BaseballWinExpModel()
    bands = model.estimate({
        "team_a": "A", "team_b": "B",
        "team_a_rating": 1500.0, "team_b_rating": 1500.0,
        "n_sims": 25, "seed": 7,
    })
    high_a = _top_band_fraction(bands, "team_a")
    high_b = _top_band_fraction(bands, "team_b")
    assert abs(high_a - high_b) < 0.35  # roughly symmetric, not a strict equality (MC noise)


def test_deterministic_with_fixed_seed():
    model = BaseballWinExpModel()
    ctx = {"team_a": "A", "team_b": "B", "team_a_rating": 1600.0, "team_b_rating": 1450.0,
           "n_sims": 15, "seed": 123}
    bands1 = model.estimate(dict(ctx))
    bands2 = model.estimate(dict(ctx))
    assert bands1 == bands2


def test_bands_sum_to_one_per_side():
    model = BaseballWinExpModel()
    bands = model.estimate({
        "team_a": "A", "team_b": "B", "team_a_rating": 1550.0, "team_b_rating": 1500.0,
        "n_sims": 20, "seed": 1,
    })
    for side in ("team_a", "team_b"):
        total = sum(b.expected_time_fraction for b in bands if b.side == side)
        assert abs(total - 1.0) < 1e-4
