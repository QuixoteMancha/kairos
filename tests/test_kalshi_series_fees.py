"""Series fee metadata is required for an exact MarketArb calculation."""

from unittest.mock import Mock

from outer_apis.kalshi.client import KalshiAPI
from strategies.market_arb import MarketArb


def test_series_fee_metadata_is_cached_and_rejects_unknown_structures():
    api = KalshiAPI()
    api._get = Mock(return_value={"series": {
        "fee_type": "quadratic", "fee_multiplier": 0.07,
    }})

    assert api.get_series_fee_metadata("KXATPMATCH") == {
        "fee_type": "quadratic", "fee_multiplier": 0.07,
    }
    assert api.get_series_fee_metadata("KXATPMATCH") is not None
    api._get.assert_called_once_with("/series/KXATPMATCH")

    api._get.reset_mock()
    api._get.return_value = {"series": {"fee_type": "flat", "fee_multiplier": 0.01}}
    assert api.get_series_fee_metadata("UNKNOWN") is None


def test_market_arb_uses_verified_series_multiplier_and_fails_closed_without_it():
    api = KalshiAPI()
    api.get_series_fee_metadata = Mock(return_value={"fee_type": "quadratic", "fee_multiplier": 0.20})
    market = {
        "id": "event", "series_ticker": "KXATPMATCH",
        "ticker_a": "a", "ticker_b": "b", "current_a_price": 0.47,
        "current_b_price": 0.47, "type": "tennis",
    }

    assert MarketArb(api, [market])._build_order_plan(market) is None

    api.get_series_fee_metadata.return_value = None
    assert MarketArb(api, [market])._build_order_plan(market) is None


def test_market_arb_does_not_fetch_fee_metadata_for_impossible_price_sum():
    api = KalshiAPI()
    api.get_series_fee_metadata = Mock()
    market = {
        "id": "event", "series_ticker": "KXATPMATCH",
        "current_a_price": 0.60, "current_b_price": 0.40, "type": "tennis",
    }

    assert MarketArb(api, [market])._build_order_plan(market) is None
    api.get_series_fee_metadata.assert_not_called()
