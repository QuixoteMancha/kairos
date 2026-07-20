# Fair-price models for traditional sports markets on Kalshi.
#
# Heuristic: simple ranking/rating differential -> logistic win probability,
# blended toward head-to-head record when available. This is a deliberately
# simplified rebuild (NOT the old Dixon-Coles/Markov architecture).
#
# Expected `market` keys (all optional beyond the base ones; TODO'd below):
#   id, type, current_a_price, current_b_price   (base, always required)
#   team_a_rating, team_b_rating                  (float, e.g. Elo-style rating)
#   h2h_a_wins, h2h_b_wins                        (int, historical head-to-head)
#
# TODO: no live data source is wired for ratings/h2h yet (that lives outside
# fairprice/, e.g. APIs/). Until that's wired, every call here hits the
# fail-closed fallback and reports zero edge.

import math

from fairprice._common import market_fallback, clamp01

# Logistic scale for rating differential -> win probability. Larger = rating
# gaps matter more. 400 mirrors the common Elo convention.
RATING_SCALE = 400.0

# Weight given to head-to-head record vs. the rating-implied probability,
# when both are available. Kept low: h2h samples are usually small.
H2H_WEIGHT = 0.15


class _SportFModel:
    """Shared logic for all sport fair-price models. Subclasses only exist
    to keep a distinct class name per market type (matches basemodel.py
    imports 1:1)."""

    def calculate_fair_price(self, market: dict) -> dict:
        rating_a = market.get('team_a_rating')
        rating_b = market.get('team_b_rating')

        if rating_a is None or rating_b is None:
            # No rating data -> can't form an independent estimate. Fail closed.
            return market_fallback(market)

        try:
            rating_a = float(rating_a)
            rating_b = float(rating_b)
        except (TypeError, ValueError):
            return market_fallback(market)

        # Standard logistic (Elo-style) win probability from rating diff.
        fair_a = 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / RATING_SCALE))

        h2h_a = market.get('h2h_a_wins')
        h2h_b = market.get('h2h_b_wins')
        if isinstance(h2h_a, (int, float)) and isinstance(h2h_b, (int, float)):
            total = h2h_a + h2h_b
            if total > 0:
                h2h_prob = h2h_a / total
                fair_a = (1 - H2H_WEIGHT) * fair_a + H2H_WEIGHT * h2h_prob

        fair_a = clamp01(fair_a)
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}


class TennisFModel(_SportFModel):
    pass


class SoccerFModel(_SportFModel):
    pass


class BasketballFModel(_SportFModel):
    pass


class BaseballFModel(_SportFModel):
    pass


class HockeyFModel(_SportFModel):
    pass
