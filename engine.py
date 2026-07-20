from strategies.limit_arb import LimitArb
from strategies.market_arb import MarketArb
from APIs.kalshi import KalshiAPI
from APIs.kalshi_tennis_markets import build_tennis_markets
from APIs.tennis_match_history import prefetch_player_histories

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

        # Parallel prefetch (thread pool, see APIs/tennis_match_history.py):
        # TennisFModel needs Flashscore recent-form/head-to-head data per
        # player, and Flashscore has no bulk endpoint (one proxied request
        # per player). Fetching sequentially inside LimitArb's per-market
        # loop below took several minutes for a full scan; warming the
        # cache for every tennis player CONCURRENTLY here first means the
        # per-market fair-price calls that follow hit an already-warm
        # cache instead of blocking on live fetches one at a time.
        tennis_player_names = [
            name
            for market in tennis_markets
            for name in (market.get('player_a_name'), market.get('player_b_name'))
            if name
        ]
        prefetch_player_histories(tennis_player_names)

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
