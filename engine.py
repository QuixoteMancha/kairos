from strategies.limit_arb import LimitArb
from strategies.market_arb import MarketArb
from APIs.kalshi import KalshiAPI
from APIs.kalshi_tennis_markets import build_tennis_markets

# Kalshi series tickers for ATP/WTA singles matches, fetched separately
# because each match is TWO per-player legs under one event_ticker (see
# APIs/kalshi_tennis_markets.py) — they need pairing before LimitArb's
# fair-price/volatility models (which compare player A vs player B) can
# use them. The generic /markets scan below still picks up each leg as
# its own single-ticket yes/no market too, which MarketArb also evaluates
# independently (same-ticket arb is valid on each leg on its own).
# The PAIRED tennis markets go to BOTH LimitArb (fair-price/volatility
# comparison) AND MarketArb (cross-ticket arb: buying "yes" on both
# players' tickers nets a guaranteed payout if underpriced -- see
# strategies/market_arb.py module docstring).
_TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH")


class Kairos:

    @staticmethod
    def run_service():
        # Initialize the Kalshi API
        kalshi_api = KalshiAPI()

        kalshi_api.initialize()
        markets = kalshi_api.get_markets()

        tennis_legs = []
        for series in _TENNIS_SERIES:
            tennis_legs.extend(kalshi_api.get_markets(series_ticker=series))
        tennis_markets = build_tennis_markets(tennis_legs)

        # LimitArb needs the paired player-A-vs-player-B tennis markets
        # for the fair-price/volatility model comparison.
        larb = LimitArb(api=kalshi_api, markets=markets + tennis_markets)
        larb.evaluate_risk_and_return_opportunities()
        larb.execute_order_placement()

        # MarketArb gets the generic scan (covers same-ticket arb on every
        # market, tennis legs included) PLUS the paired tennis markets
        # (covers the cross-ticket player-A/player-B arb -- see
        # strategies/market_arb.py module docstring).
        market_arb = MarketArb(api=kalshi_api, markets=markets + tennis_markets)
        market_arb.evaluate_risk_and_return_opportunities()
        market_arb.execute_order_placement()

        return {
            'limit_arb_opportunities': larb.opportunities,
            'market_arb_opportunities': market_arb.opportunities,
        }
