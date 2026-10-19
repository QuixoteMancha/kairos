"""Order plans must preserve the economics of same- and cross-ticker arbs."""

from strategies.market_arb import MarketArb


def test_cross_ticker_arb_buys_yes_on_both_complementary_markets():
    plan = MarketArb(None, [])._build_order_plan({
        "id": "event", "ticker_a": "player-a", "ticker_b": "player-b",
        "current_a_price": 0.40, "current_b_price": 0.40, "type": "tennis",
    })

    assert plan is not None
    assert plan["market_leg_a"]["side"] == "a"
    assert plan["market_leg_b"] == {
        "ticker": "player-b", "side": "a", "action": "market", "price": 0.40,
    }


def test_single_ticker_arb_keeps_the_complementary_no_leg():
    plan = MarketArb(None, [])._build_order_plan({
        "id": "binary", "current_a_price": 0.40, "current_b_price": 0.40,
    })

    assert plan is not None
    assert plan["market_leg_a"]["side"] == "a"
    assert plan["market_leg_b"]["side"] == "b"
