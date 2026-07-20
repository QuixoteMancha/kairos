from strategies.limit_arb import LimitArb
from strategies.market_arb import MarketArb
from APIs.kalshi import KalshiAPI


class Kairos:

    @staticmethod
    def run_service():
        # Initialize the Kalshi API
        kalshi_api = KalshiAPI()

        kalshi_api.initialize()
        markets = kalshi_api.get_markets()

        larb = LimitArb(api=kalshi_api, markets=markets)
        larb.evaluate_risk_and_return_opportunities()
        larb.execute_order_placement()

        market_arb = MarketArb(api=kalshi_api, markets=markets)
        market_arb.evaluate_risk_and_return_opportunities()
        market_arb.execute_order_placement()

        return {
            'limit_arb_opportunities': larb.opportunities,
            'market_arb_opportunities': market_arb.opportunities,
        }
