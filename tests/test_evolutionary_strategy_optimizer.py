from __future__ import annotations

from scripts.tennis.evolutionary_strategy_optimizer import evolve_strategy_rules


def _case(year: int, index: int, *, good: bool) -> dict:
    return {
        "match_id": f"{year}-{index}",
        "date": f"{year}-06-{index % 28 + 1:02d}",
        "favorite_decimal_odds": 2.0,
        "market_probability": 0.5,
        "model_probability": 0.72 if good else 0.51,
        "weighted_elo_diff": 150.0 if good else -40.0,
        "recent_wr_diff": 0.25 if good else -0.15,
        "favorite_elo_rank": 10,
        "hit": good,
    }


def _predictions() -> dict[str, list[dict]]:
    cases = []
    for year in (2024, 2025, 2026):
        cases.extend(_case(year, index, good=index < 40) for index in range(80))
    return {"synthetic": cases}


def test_evolution_is_deterministic_and_keeps_holdout_out_of_fitness():
    first = evolve_strategy_rules(
        _predictions(), seed=7, population_size=16, generations=8,
        minimum_trades_per_fold=20,
    )
    second = evolve_strategy_rules(
        _predictions(), seed=7, population_size=16, generations=8,
        minimum_trades_per_fold=20,
    )

    assert first == second
    assert first["evolution"]["validation_years"] == [2024, 2025]
    assert first["evolution"]["holdout_year"] == 2026
    assert [fold["year"] for fold in first["validation_folds"]] == [2024, 2025]
    assert first["holdout_fold"]["year"] == 2026
    assert first["eligible_for_review"] is True


def test_evolution_keeps_single_validation_year_observational_only():
    report = evolve_strategy_rules(
        {"synthetic": _predictions()["synthetic"][:-80]},
        population_size=8, generations=2,
    )

    assert report["eligible_for_review"] is False
    assert report["reason"] == "only one annual validation fold; observation only"
    assert report["evolution"]["validation_years"] == [2024]
    assert report["evolution"]["holdout_year"] == 2025
