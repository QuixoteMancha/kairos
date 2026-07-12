import pytest

from src.core.strategies.overvaluation_gap import (
    compute_overvaluation_gap,
    weight_gap_by_category_volatility,
    derive_limit_level,
    max_order_size_from_liquidity,
)


class TestComputeOvervaluationGap:
    def test_positive_gap_means_favorite_overvalued(self):
        gap = compute_overvaluation_gap(market_price=0.70, fair_prob=0.55)
        assert gap == pytest.approx(0.15)
        assert gap > 0

    def test_negative_gap_means_favorite_undervalued(self):
        gap = compute_overvaluation_gap(market_price=0.55, fair_prob=0.70)
        assert gap == pytest.approx(-0.15)
        assert gap < 0

    def test_zero_gap(self):
        assert compute_overvaluation_gap(0.6, 0.6) == pytest.approx(0.0)

    def test_rejects_market_price_out_of_range(self):
        with pytest.raises(ValueError):
            compute_overvaluation_gap(market_price=1.5, fair_prob=0.5)

    def test_rejects_fair_prob_out_of_range(self):
        with pytest.raises(ValueError):
            compute_overvaluation_gap(market_price=0.5, fair_prob=-0.1)


class TestWeightGapByCategoryVolatility:
    def test_baseball_flagged_as_no_dynamic_epsilon(self):
        result = weight_gap_by_category_volatility(0.1, "baseball", {})
        assert result["dynamic_epsilon_available"] is False
        assert "category_not_calibrated" in result["epsilon_source"]
        assert result["gap"] == 0.1
        assert isinstance(result["epsilon"], float)

    def test_unsupported_category_also_flagged_unavailable(self):
        result = weight_gap_by_category_volatility(0.1, "curling", {})
        assert result["dynamic_epsilon_available"] is False
        assert "category_unsupported" in result["epsilon_source"]

    def test_supported_category_with_no_model_context_falls_back_but_is_labelled(self):
        # tennis is a supported category but an empty context will not
        # produce usable bands -> falls back, and must say so explicitly.
        result = weight_gap_by_category_volatility(-0.05, "tennis", {})
        assert result["gap"] == -0.05
        assert "epsilon_source" in result
        # Whatever the outcome, availability flag must be a real bool, never
        # silently absent.
        assert isinstance(result["dynamic_epsilon_available"], bool)


class TestDeriveLimitLevel:
    def test_basic_offset(self):
        level = derive_limit_level(current_price=0.55, epsilon=0.05, side="favorite")
        assert level == pytest.approx(0.50)

    def test_clamped_to_lower_bound(self):
        level = derive_limit_level(current_price=0.21, epsilon=0.5, side="underdog")
        assert level == 0.20

    def test_clamped_to_upper_bound_with_zero_epsilon_extreme_price(self):
        level = derive_limit_level(current_price=0.99, epsilon=0.0, side="favorite")
        assert level == 0.80

    def test_never_out_of_band_for_extreme_inputs(self):
        for price in (0.0, 0.01, 0.5, 0.99, 1.0):
            for eps in (0.0, 0.01, 0.5, 10.0):
                level = derive_limit_level(current_price=price, epsilon=eps, side="favorite")
                assert 0.20 <= level <= 0.80

    def test_rejects_invalid_side(self):
        with pytest.raises(ValueError):
            derive_limit_level(0.5, 0.05, side="banana")

    def test_rejects_negative_epsilon(self):
        with pytest.raises(ValueError):
            derive_limit_level(0.5, -0.01, side="favorite")

    def test_rejects_price_out_of_range(self):
        with pytest.raises(ValueError):
            derive_limit_level(1.2, 0.05, side="favorite")


class TestMaxOrderSizeFromLiquidity:
    def test_uses_avg_volume_when_available(self):
        size = max_order_size_from_liquidity(book_depth_at_level=1000.0, avg_volume=50_000.0)
        assert size == pytest.approx(500.0)

    def test_falls_back_to_book_depth_when_no_avg_volume(self):
        size = max_order_size_from_liquidity(book_depth_at_level=2000.0, avg_volume=None)
        assert size == pytest.approx(20.0)

    def test_falls_back_to_book_depth_when_avg_volume_zero(self):
        size = max_order_size_from_liquidity(book_depth_at_level=2000.0, avg_volume=0.0)
        assert size == pytest.approx(20.0)

    def test_fail_closed_when_both_missing(self):
        assert max_order_size_from_liquidity(book_depth_at_level=0.0, avg_volume=None) == 0.0

    def test_fail_closed_when_both_zero(self):
        assert max_order_size_from_liquidity(book_depth_at_level=0.0, avg_volume=0.0) == 0.0

    def test_fail_closed_with_negative_values(self):
        assert max_order_size_from_liquidity(book_depth_at_level=-5.0, avg_volume=-10.0) == 0.0
