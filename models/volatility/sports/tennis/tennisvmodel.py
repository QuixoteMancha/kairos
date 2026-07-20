"""Tennis volatility via a point-level Markov chain + Monte Carlo (see
`tennis_markov.py` in this package) instead of the generic price/time
heuristic used by the other sports (see `_base.py`) — tennis is the one
sport in this package with a mathematically tractable scoring structure
(game/set/match derived analytically from p_serve), so it gets a dedicated
model rather than sharing `_SportVModelBase`.
"""

from models.volatility import safe_price, clamp01
from .tennis_markov import p_serve_for_rank, simulate_win_prob_volatility


class TennisVModel:
    """Does NOT inherit `_SportVModelBase`: it has no time-to-resolution
    input and no TIME_HORIZON_HOURS concept — the Markov/Monte Carlo
    engine's output already encodes "how much can the win probability
    move", derived from score-state dynamics, not from a generic
    time-decay heuristic."""

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

        # Optional: "ATP" or "WTA" selects the real-calibrated p_serve
        # table for that tour (see tennis_markov.py); missing/unrecognized
        # falls back to the generic static placeholder rather than
        # guessing which tour's curve applies.
        tour = market.get("tour")

        pa_serve = p_serve_for_rank(rank_a, tour=tour)
        pb_serve = p_serve_for_rank(rank_b, tour=tour)
        volatility = simulate_win_prob_volatility(pa_serve, pb_serve, best_of=best_of)
        return clamp01(volatility)
