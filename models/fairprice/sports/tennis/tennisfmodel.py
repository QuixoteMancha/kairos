# Real fair-price via the tennis Markov engine (models/volatility/sports/
# tennis/tennis_markov.py) instead of the generic Elo-style _SportFModel
# heuristic — tennis is the one sport with a mathematically tractable
# scoring structure (game/set/match derived analytically from p_serve),
# and that same engine already powers TennisVModel's volatility, so this
# reuses it for fair-price too instead of maintaining a second, unrelated
# rating system for the same sport.
#
# CONTEXT (see kairos-tennis-critical-bugs-and-model-risk-2026-07-20
# memory): a rank-bucket-ONLY fair price disagreed with a real liquid
# Kalshi market by ~40 percentage points on a case where the market
# almost certainly priced in recent form/injury history that pure
# ranking doesn't capture (a player ranked 221 who the market still
# treated as a heavy favorite, e.g. a former top-30 player returning from
# injury). Blending in REAL recent-form and head-to-head data (via
# outer_apis/tennis_match_history.py, Flashscore-sourced) doesn't fully close
# that gap — it's still a simple signal, not real injury/surface/context
# modeling — but it's real data instead of nothing, and should narrow the
# rank-only model's blind spot somewhat.

from models.fairprice._common import clamp01
from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

from .._base import _SportFModel

# Weight given to the recent-form differential (last N matches' win rate)
# on top of the rank-based estimate, when both players' form is available.
# Kept modest, same rationale as _base.py's H2H_WEIGHT: a simple win-rate
# signal is noisy over small samples, shouldn't dominate the rank-based
# estimate.
FORM_WEIGHT = 0.15

# Weight given to head-to-head record, applied AFTER the form blend (so
# the final mix, when everything is available, is
# ~72% rank / 13% form / 15% h2h -- h2h gets a similar modest weight to
# form, but is applied second since a real head-to-head meeting is a
# stronger signal than general recent form when it exists).
H2H_WEIGHT = 0.15
MIN_H2H_MATCHES = 2  # fail-closed: fewer real meetings than this -> not enough signal, skip.

RECENT_FORM_LOOKBACK = 10
H2H_LOOKBACK = 40


class TennisFModel(_SportFModel):
    """Fail-closed: if player_a_rank/player_b_rank aren't present/valid
    positive integers, falls back to the generic _SportFModel heuristic
    (rating-based, itself fail-closed to the market price if no rating
    either) rather than fabricating a probability from missing rank data.
    Form/h2h blending is itself fail-closed and independently optional:
    missing player names, missing/insufficient real match history, or a
    scraper failure all just skip that specific blend step (see
    outer_apis/tennis_match_history.py), never fabricate a signal."""

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

        fair_a = self._blend_recent_form(fair_a, market)
        fair_a = self._blend_head_to_head(fair_a, market)

        fair_a = clamp01(fair_a)
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}

    @staticmethod
    def _blend_recent_form(fair_a: float, market: dict) -> float:
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not name_a or not name_b:
            return fair_a  # no names -> can't look up real history, skip.

        from outer_apis.sports.tennis.match_history import get_recent_form

        form_a = get_recent_form(name_a, n=RECENT_FORM_LOOKBACK)
        form_b = get_recent_form(name_b, n=RECENT_FORM_LOOKBACK)
        if form_a is None or form_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        form_implied_a = clamp01(0.5 + (form_a['win_rate'] - form_b['win_rate']) / 2)
        return (1 - FORM_WEIGHT) * fair_a + FORM_WEIGHT * form_implied_a

    @staticmethod
    def _blend_head_to_head(fair_a: float, market: dict) -> float:
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.match_history import get_head_to_head

        h2h = get_head_to_head(name_a, name_b, lookback=H2H_LOOKBACK)
        if h2h is None or h2h['n_matches'] < MIN_H2H_MATCHES:
            return fair_a  # fail-closed: no data or too few real meetings, skip.

        h2h_implied_a = h2h['a_wins'] / h2h['n_matches']
        return (1 - H2H_WEIGHT) * fair_a + H2H_WEIGHT * h2h_implied_a
