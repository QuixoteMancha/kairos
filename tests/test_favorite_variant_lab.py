from scripts.tennis.favorite_variant_lab import (
    PRESETS, FilterPreset, _favorite_side, _slope, dataset_sha256,
)


def test_presets_keep_market_and_model_experiments_named():
    assert {
        "baseline", "surface_elo_selector", "combined_50_50_selector",
        "surface_52w", "surface_and_form", "momentum_no_grass", "strict_edge",
    } <= set(PRESETS)
    assert PRESETS["strict_edge"].min_edge == 0.03
    assert PRESETS["strict_edge"].max_odds == 3.0


def test_slope_distinguishes_rising_and_declining_form():
    assert _slope([1500, 1510, 1520, 1530, 1540]) > 0
    assert _slope([1540, 1530, 1520, 1510, 1500]) < 0


def test_dataset_hash_includes_features_not_only_match_identity():
    case = {"match_id": "1", "date": "2026-01-01", "model_edge": 0.03}
    assert dataset_sha256([case]) != dataset_sha256([{**case, "model_edge": 0.04}])


def test_filter_preset_defaults_are_research_baseline():
    assert FilterPreset().min_edge is None
    assert FilterPreset().exclude_grass is False
