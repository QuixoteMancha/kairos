from strategies.limit_arb import LimitArb
from strategies.market_arb import YNArb
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

        yn_arb = YNArb(api=kalshi_api, markets=markets)
        yn_arb.evaluate_risk_and_return_opportunities()
        yn_arb.execute_order_placement()

        return {
            'limit_arb_opportunities': larb.opportunities,
            'yn_arb_opportunities': yn_arb.opportunities,
        }
