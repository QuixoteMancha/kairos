from scripts.tennis.bet_on_favourite_backtest import simulate, run_walk_forward


def _case(match_id, date, hit, odds=2.0):
    return {
        "match_id": match_id,
        "date": date,
        "favorite_decimal_odds": odds,
        "raw_model_probability": 0.75,
        "tour": "ATP",
        "surface": "Hard",
        "hit": hit,
    }


def test_simulate_uses_decimal_odds_and_daily_starting_capital():
    result = simulate([
        _case("a", "2025-01-01", True),
        _case("b", "2025-01-01", False),
    ], initial_capital=100.0)
    assert result["n_trades"] == 2
    assert result["final_capital"] == 100.0


def test_walk_forward_never_includes_test_year_in_training_count():
    cases = [
        _case("a", "2024-01-01", True),
        _case("b", "2025-01-01", True),
        _case("c", "2025-02-01", False),
        _case("d", "2026-01-01", True),
    ]
    folds = run_walk_forward(cases)
    assert [(f["test_year"], f["train_n"], f["candidate_n"]) for f in folds] == [
        (2025, 1, 2),
        (2026, 3, 1),
    ]


def test_simulate_enforces_daily_exposure_cap():
    cases = [_case(str(i), "2025-01-01", True) for i in range(60)]
    result = simulate(cases, initial_capital=100.0)
    assert result["n_trades"] == 50
    assert result["skipped_daily_cap"] == 10
