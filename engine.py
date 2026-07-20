from strat.limit_arb import LimitArb
from strat.yn_arb import YNArb
from kalshi_api import KalshiAPI


class Kairos:

    @staticmethod
    def run_service():
        # Initialize the Kalshi API
        kalshi_api = KalshiAPI()

        kalshi_api.initialize()
        markets = kalshi_api.get_markets(category="sports")

        larb = LimitArb(markets=markets)
        larb.evaluate_risk_and_return_opportunities()
        larb.execute_order_placement()

        yn_arb = YNArb(markets=markets)
        yn_arb.evaluate_risk_and_return_opportunities()
        yn_arb.execute_order_placement()
