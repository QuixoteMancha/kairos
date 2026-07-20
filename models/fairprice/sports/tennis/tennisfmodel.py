# Real fair-price via the tennis Markov engine (models/volatility/sports/
# tennis/tennis_markov.py) instead of the generic Elo-style _SportFModel
# heuristic — tennis is the one sport with a mathematically tractable
# scoring structure (game/set/match derived analytically from p_serve),
# and that same engine already powers TennisVModel's volatility, so this
# reuses it for fair-price too instead of maintaining a second, unrelated
# rating system for the same sport.

from models.fairprice._common import clamp01
from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

from .._base import _SportFModel


class TennisFModel(_SportFModel):
    """Fail-closed: if player_a_rank/player_b_rank aren't present/valid
    positive integers, falls back to the generic _SportFModel heuristic
    (rating-based, itself fail-closed to the market price if no rating
    either) rather than fabricating a probability from missing rank data."""

    def calculate_fair_price(self, market: dict) -> dict:
        rank_a = market.get('player_a_rank')
        rank_b = market.get('player_b_rank')

        if not isinstance(rank_a, int) or isinstance(rank_a, bool) or rank_a <= 0:
            return super().calculate_fair_price(market)
        if not isinstance(rank_b, int) or isinstance(rank_b, bool) or rank_b <= 0:
            return super().calculate_fair_price(market)

        best_of = market.get('best_of', 3)
        if best_of not in (3, 5):
            return super().calculate_fair_price(market)

        tour = market.get('tour')

        try:
            fair_a = pre_match_win_prob(rank_a, rank_b, best_of=best_of, tour=tour)
        except ValueError:
            # defensive: pre_match_win_prob's own validation should never
            # trigger given the checks above, but never let an unexpected
            # exception here silently corrupt an arb decision -- fall back
            # to the generic heuristic instead of raising.
            return super().calculate_fair_price(market)

        fair_a = clamp01(fair_a)
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}
