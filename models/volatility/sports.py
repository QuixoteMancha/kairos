"""
Volatility models for traditional-sports-style binary prediction markets
(tennis, soccer, basketball, baseball, hockey).

NOTE: Kairos no longer bets on traditional sports directly (pivoted to
Kalshi-only prediction markets) — these classes exist because
models/basemodel.py still registers a volatility model per market
`type` key that may appear on Kalshi (e.g. sports-outcome markets).

Heuristic (documented, deliberately simple — no Markov/Dixon-Coles):

    volatility = distance_from_certainty(price) * time_factor(hours_left)

- distance_from_certainty(price): peaks at price == 0.5 (max uncertainty,
  max room to swing), goes to 0 as price -> 0 or 1 (already resolved in
  the market's eyes).
- time_factor(hours_left): approaches 1.0 as hours_left grows past the
  sport's typical event/market duration (TIME_HORIZON_HOURS), approaches
  0 as resolution nears (nothing left to move). This is a simple
  saturating ramp: min(1, hours_left / TIME_HORIZON_HOURS).
- Each sport's TIME_HORIZON_HOURS is a TODO-flagged placeholder based on
  typical event length; not calibrated against historical price paths.
- Final score is the average of the two legs' distance_from_certainty
  (using current_a_price and current_b_price) to be robust to which leg
  was passed as "current price", then scaled by time_factor.

Fail-closed: missing/invalid prices or missing time-to-resolution => 0.0.
"""

from . import safe_price, safe_hours_to_resolution, distance_from_certainty, clamp01
from .tennis_markov import p_serve_for_rank, simulate_win_prob_volatility


class _SportVModelBase:
    # TODO: calibrate against real historical Kalshi sports-market price
    # paths instead of this placeholder "typical event length" heuristic.
    TIME_HORIZON_HOURS = 3.0

    def _time_factor(self, hours_left: float) -> float:
        if self.TIME_HORIZON_HOURS <= 0:
            return 0.0
        return clamp01(hours_left / self.TIME_HORIZON_HOURS)

    def calculate_volatility(self, market: dict) -> float:
        price_a = safe_price(market, "current_a_price")
        price_b = safe_price(market, "current_b_price")
        hours_left = safe_hours_to_resolution(market)

        # Fail-closed: any missing/invalid required input => report zero
        # volatility rather than assume high volatility.
        if price_a is None or price_b is None or hours_left is None:
            return 0.0

        uncertainty = (distance_from_certainty(price_a) + distance_from_certainty(price_b)) / 2.0
        return clamp01(uncertainty * self._time_factor(hours_left))


class TennisVModel:
    """Tennis volatility via a point-level Markov chain + Monte Carlo
    (see `models/volatility/tennis_markov.py`) instead of the generic
    price/time heuristic used by the other sports below — tennis is the
    one sport in this package with a mathematically tractable scoring
    structure (game/set/match derived analytically from p_serve), so it
    gets a dedicated model rather than sharing `_SportVModelBase`.

    Does NOT inherit `_SportVModelBase`: it has no time-to-resolution input
    and no TIME_HORIZON_HOURS concept — the Markov/Monte Carlo engine's
    output already encodes "how much can the win probability move", derived
    from score-state dynamics, not from a generic time-decay heuristic.
    """

    def calculate_volatility(self, market: dict) -> float:
        price_a = safe_price(market, "current_a_price")
        price_b = safe_price(market, "current_b_price")
        if price_a is None or price_b is None:
            return 0.0

        rank_a = market.get("player_a_rank")
        rank_b = market.get("player_b_rank")
        # Fail-closed: rankings must be present and positive integers. Never
        # fabricate a ranking for a missing/invalid value.
        if not isinstance(rank_a, int) or isinstance(rank_a, bool) or rank_a <= 0:
            return 0.0
        if not isinstance(rank_b, int) or isinstance(rank_b, bool) or rank_b <= 0:
            return 0.0

        best_of = market.get("best_of", 3)
        if best_of not in (3, 5):
            return 0.0

        pa_serve = p_serve_for_rank(rank_a)
        pb_serve = p_serve_for_rank(rank_b)
        volatility = simulate_win_prob_volatility(pa_serve, pb_serve, best_of=best_of)
        return clamp01(volatility)


class SoccerVModel(_SportVModelBase):
    # TODO: placeholder — typical 90-min match + stoppage.
    TIME_HORIZON_HOURS = 2.0


class BasketballVModel(_SportVModelBase):
    # TODO: placeholder — typical NBA/NCAA game window.
    TIME_HORIZON_HOURS = 2.5


class BaseballVModel(_SportVModelBase):
    # TODO: placeholder — typical MLB game window (longer, variable length).
    TIME_HORIZON_HOURS = 3.5


class HockeyVModel(_SportVModelBase):
    # TODO: placeholder — typical NHL game window.
    TIME_HORIZON_HOURS = 2.75
