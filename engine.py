from strategies.limit_arb import LimitArb
from strategies.market_arb import MarketArb
from outer_apis.kalshi import KalshiAPI
from outer_apis.kalshi_tennis_markets import build_tennis_markets
from outer_apis.tennis_match_history import prefetch_player_histories
from outer_apis.kalshi_football_markets import build_football_markets
from outer_apis.kalshi_team_sport_markets import build_team_sport_markets

# Kalshi series tickers for ATP/WTA singles matches, fetched separately
# because each match is TWO per-player legs under one event_ticker (see
# outer_apis/kalshi_tennis_markets.py) — they need pairing before LimitArb's
# fair-price/volatility models (which compare player A vs player B) can
# use them. The generic /markets scan below still picks up each leg as
# its own single-ticket yes/no market too, which MarketArb also evaluates
# independently (same-ticket arb is valid on each leg on its own).
# The PAIRED tennis markets go to BOTH LimitArb (fair-price/volatility
# comparison) AND MarketArb (cross-ticket arb: buying "yes" on both
# players' tickers nets a guaranteed payout if underpriced -- see
# strategies/market_arb.py module docstring).
_TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH")

# Kalshi series for soccer/football moneyline markets (3-way per event:
# team A / team B / tie). Reduced to a single-leg binary (favorite's own
# yes/no) per market — see outer_apis/kalshi_football_markets.py module
# docstring for the confirmed design decision. Only feeds LimitArb (needs
# the real Elo-enriched dict); MarketArb doesn't need this at all, every
# leg already flows through the generic scan below on its own.
_FOOTBALL_SERIES = ("KXMLSGAME", "KXUEFAGAME")

# Kalshi series for the 4 two-way team sports (MLB/NBA/NHL/NFL, no draws
# -- unlike soccer, these pair cross-ticket exactly like tennis, see
# outer_apis/kalshi_team_sport_markets.py module docstring). Feeds BOTH
# LimitArb and MarketArb, same as tennis. KXNBAGAME/KXNHLGAME had zero
# open markets at verification time (NBA/NHL off-season in July) --
# built and wired anyway, will activate automatically once those seasons
# open, not independently verified live yet.
_TEAM_SPORT_SERIES = ("KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXNFLGAME")


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

        # Parallel prefetch (thread pool, see outer_apis/tennis_match_history.py):
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

        football_legs = []
        for series in _FOOTBALL_SERIES:
            football_legs.extend(kalshi_api.get_markets(series_ticker=series))
        football_markets = build_football_markets(football_legs)

        team_sport_markets = []
        for series in _TEAM_SPORT_SERIES:
            legs = kalshi_api.get_markets(series_ticker=series)
            team_sport_markets.extend(build_team_sport_markets(legs, series))

        # LimitArb needs the paired player-A-vs-player-B tennis markets,
        # the Elo-enriched favorite-only football markets, and the paired
        # team-vs-team markets for the fair-price/volatility comparison.
        larb = LimitArb(api=kalshi_api, markets=markets + tennis_markets + football_markets + team_sport_markets)
        larb.evaluate_risk_and_return_opportunities()
        larb.execute_order_placement()

        # MarketArb gets the generic scan (covers same-ticket arb on every
        # market, tennis/football/team-sport legs included) PLUS the
        # paired tennis and team-sport markets (covers the cross-ticket
        # arb -- see strategies/market_arb.py module docstring). Football
        # is NOT added here: its favorite-only markets aren't a genuine
        # cross-ticket pair (see outer_apis/kalshi_football_markets.py).
        market_arb = MarketArb(api=kalshi_api, markets=markets + tennis_markets + team_sport_markets)
        market_arb.evaluate_risk_and_return_opportunities()
        market_arb.execute_order_placement()

        return {
            'limit_arb_opportunities': larb.opportunities,
            'market_arb_opportunities': market_arb.opportunities,
        }
