"""Tests for src.core.strategies.limit_order_formation (read-only, no order execution)."""
from unittest.mock import MagicMock, patch

from src.core.strategies.dwell_time.base import DwellBand
from src.core.strategies.limit_order_formation import (
    propose_limit_levels,
    propose_and_check_fillable,
    estimate_dynamic_epsilon,
)


def _fake_model(bands):
    m = MagicMock()
    m.estimate.return_value = bands
    return m


def test_top_band_pair_forms_valid_arb():
    # team_a mostly at 0.30 (cheap favorite-underdog framing), team_b mostly at 0.60.
    # 0.30 + 0.60 = 0.90 < 1.0 -> valid on the very first (most-favored) pair.
    bands = [
        DwellBand(low=0.25, high=0.35, side="team_a", expected_time_fraction=0.6),
        DwellBand(low=0.55, high=0.65, side="team_a", expected_time_fraction=0.4),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.6),
        DwellBand(low=0.25, high=0.35, side="team_b", expected_time_fraction=0.4),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels("basketball", {"team_a": "Lakers", "team_b": "Celtics"})
    assert result["opportunity"] is True
    assert result["sides"]["team_a"]["price"] == 0.30
    assert result["sides"]["team_b"]["price"] == 0.60
    assert result["implied_edge"] == 0.10


def test_fallback_to_second_band_when_top_pair_invalid():
    # Most-favored bands (0.60 + 0.60 = 1.20) are invalid; the top/second-band
    # combo (0.60 team_a + 0.30 team_b = 0.90) IS valid and should be picked.
    bands = [
        DwellBand(low=0.55, high=0.65, side="team_a", expected_time_fraction=0.7),
        DwellBand(low=0.25, high=0.35, side="team_a", expected_time_fraction=0.3),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.7),
        DwellBand(low=0.25, high=0.35, side="team_b", expected_time_fraction=0.3),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels("baseball", {"team_a": "Yankees", "team_b": "Red Sox"})
    assert result["opportunity"] is True
    prices = {result["sides"]["team_a"]["price"], result["sides"]["team_b"]["price"]}
    assert prices == {0.60, 0.30}
    assert result["implied_edge"] == 0.10


def test_no_viable_combination_returns_no_opportunity():
    # Every band pair sums to >= 1.0 (bands cluster near 0.60/0.60, 0.60/0.55, etc.)
    bands = [
        DwellBand(low=0.55, high=0.65, side="team_a", expected_time_fraction=0.6),
        DwellBand(low=0.50, high=0.60, side="team_a", expected_time_fraction=0.4),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.6),
        DwellBand(low=0.50, high=0.60, side="team_b", expected_time_fraction=0.4),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels("basketball", {"team_a": "Lakers", "team_b": "Celtics"})
    assert result["opportunity"] is False
    assert "reason" in result


def test_current_price_filters_out_in_the_money_bands():
    # team_a's only band (0.60) is NOT cheaper than the current market price (0.55),
    # so it should be filtered out entirely -> no candidates -> no opportunity.
    bands = [
        DwellBand(low=0.55, high=0.65, side="team_a", expected_time_fraction=1.0),
        DwellBand(low=0.25, high=0.35, side="team_b", expected_time_fraction=1.0),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels(
            "basketball", {"team_a": "Lakers", "team_b": "Celtics"},
            current_price_a=0.55, current_price_b=0.50,
        )
    assert result["opportunity"] is False


def test_unsupported_category_rejected():
    result = propose_limit_levels("weather", {})
    assert result["opportunity"] is False
    assert "not supported" in result["reason"]


def test_gross_edge_field_present_and_equal_to_net_when_platform_unknown():
    # Regression: implied_edge must be NET of fees, implied_edge_gross must be
    # the RAW (1 - price_a - price_b). With no platform passed, fee netting
    # is skipped (documented limitation), so both should collapse to the same
    # raw value -- but the gross field must always be present once an
    # opportunity is formed, so callers can't silently mistake net for gross.
    bands = [
        DwellBand(low=0.25, high=0.35, side="team_a", expected_time_fraction=0.6),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.6),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels("basketball", {"team_a": "Lakers", "team_b": "Celtics"})
    assert result["opportunity"] is True
    assert result["implied_edge_gross"] == 0.10
    assert result["implied_edge"] == 0.10


def test_implied_edge_nets_out_platform_fees_when_platform_known():
    # Regression for the fee-netting bug: with platform="kalshi", implied_edge
    # (net) must be strictly less than implied_edge_gross once a real
    # per-platform fee is applied -- this is the field downstream code must
    # gate/sort on, never the gross one.
    bands = [
        DwellBand(low=0.25, high=0.35, side="team_a", expected_time_fraction=0.6),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.6),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_limit_levels(
            "basketball", {"team_a": "Lakers", "team_b": "Celtics"}, platform="kalshi",
        )
    assert result["opportunity"] is True
    assert result["implied_edge_gross"] == 0.10
    assert result["implied_edge"] < result["implied_edge_gross"]


def test_propose_and_check_fillable_combines_levels_with_real_book():
    bands = [
        DwellBand(low=0.25, high=0.35, side="team_a", expected_time_fraction=0.6),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=0.6),
    ]
    fake_client = MagicMock()
    fake_client.get_order_book.return_value = {
        "bids": [], "asks": [(0.30, 1000.0), (0.35, 500.0)],
    }
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_and_check_fillable(
            "basketball", {"team_a": "Lakers", "team_b": "Celtics"},
            capital_tiers=[50.0, 5000.0],
            token_id_a="token-a-123",
            _client=fake_client,
        )
    assert result["opportunity"] is True
    assert "team_a" in result["fillability"]
    assert "team_b" not in result["fillability"]  # no token_id_b passed
    sims = result["fillability"]["team_a"]
    assert [s["capital_usd"] for s in sims] == [50.0, 5000.0]
    assert sims[0]["fill_pct"] == 100.0  # $50 well within depth at 0.30
    assert sims[1]["depth_exhausted"] is True  # $5000 exceeds available depth
    fake_client.close.assert_not_called()  # injected client — caller owns lifecycle


def test_propose_and_check_fillable_no_opportunity_skips_book_lookup():
    bands = [
        DwellBand(low=0.55, high=0.65, side="team_a", expected_time_fraction=1.0),
        DwellBand(low=0.55, high=0.65, side="team_b", expected_time_fraction=1.0),
    ]
    fake_client = MagicMock()
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        result = propose_and_check_fillable(
            "basketball", {"team_a": "Lakers", "team_b": "Celtics"},
            token_id_a="token-a", _client=fake_client,
        )
    assert result["opportunity"] is False
    assert result["fillability"] == {}
    fake_client.get_order_book.assert_not_called()


# ── estimate_dynamic_epsilon: dwell-time -> Strategy 1 level offset wiring ──

def test_estimate_dynamic_epsilon_uses_model_when_available():
    bands = [
        DwellBand(low=0.40, high=0.60, side="team_a", expected_time_fraction=1.0),
        DwellBand(low=0.40, high=0.60, side="team_b", expected_time_fraction=1.0),
    ]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        eps, source = estimate_dynamic_epsilon("football", {"team_a": "A", "team_b": "B"}, fallback=0.03)
    assert source == "dwell_time:football"
    assert 0.01 <= eps <= 0.08


def test_estimate_dynamic_epsilon_fallback_baseball_always_uncalibrated():
    # Baseball is explicitly banned as a volatility source regardless of
    # whether a model is registered/returns bands — never used per explicit
    # user instruction (its own docstring says "approximation, not
    # calibrated").
    bands = [DwellBand(low=0.1, high=0.9, side="team_a", expected_time_fraction=1.0)]
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=_fake_model(bands)):
        eps, source = estimate_dynamic_epsilon("baseball", {"team_a": "A", "team_b": "B"}, fallback=0.03)
    assert eps == 0.03
    assert source.startswith("fallback_generic:category_not_calibrated")


def test_estimate_dynamic_epsilon_fallback_no_model_registered():
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=None):
        eps, source = estimate_dynamic_epsilon("basketball", {"team_a": "A", "team_b": "B"}, fallback=0.03)
    assert eps == 0.03
    assert source.startswith("fallback_generic:no_model")


def test_estimate_dynamic_epsilon_fallback_on_model_error():
    broken = MagicMock()
    broken.estimate.side_effect = RuntimeError("boom")
    with patch("src.core.strategies.limit_order_formation.get_model", return_value=broken):
        eps, source = estimate_dynamic_epsilon("football", {"team_a": "A", "team_b": "B"}, fallback=0.03)
    assert eps == 0.03
    assert source.startswith("fallback_generic:model_error")
